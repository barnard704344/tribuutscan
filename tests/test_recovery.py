"""Loss/reacquisition acceptance and pacing, without requiring a physical GPU."""
from contextlib import contextmanager
from unittest.mock import Mock, patch
import numpy as np
from tribuutscan import cloud as cl, livescan
from tribuutscan.recovery import SearchBudget


def test_search_budget_rotates_available_methods_and_caps_timestamp_and_frame_cadence():
    budget=SearchBudget()
    methods={'object features','scene features','shape'}
    assert budget.choose(1.,0,methods)=='object features'
    assert budget.choose(1.01,1,methods) is None
    assert budget.choose(1.2,2,methods)=='scene features'
    assert budget.choose(1.4,3,methods)=='shape'
    assert budget.choose(1.6,4,{'object features'})=='object features'
    assert budget.choose(1.8,5,set()) is None
    assert budget.choose(1.8,6,{'shape'})=='shape'
    # A restarted input clock must not silence recovery indefinitely.
    assert budget.choose(.1,7,methods)=='object features'
    budget=SearchBudget()
    tries=[budget.choose(None,i,methods) for i in range(30)]
    assert sum(method is not None for method in tries)==5


@contextmanager
def scanner(cpu=False):
    from test_cloud import lump
    s=cl.Scanner(log=lambda *_:None,track_with='joint',mode='free')
    xyz=lump(np.random.default_rng(33),1300,50,300)
    pix=np.c_[np.arange(len(xyz))%640,np.arange(len(xyz))//640]
    s.prev=True;s.last_t=1.;s.lost_run=5;s.last_merged=np.eye(4);s.stats.update(merged=3,tracking=False)
    s.merge_t=1e9
    s.features=None;s.frame_markers=Mock(return_value=np.empty((0,3)))
    step=np.eye(4);step[0,3]=.004
    if cpu:
        s.step_due=Mock(return_value=True);s.joint_step=Mock(return_value=(step,True))
    else:
        s.gpu=Mock();s.track_with='gpu';s.gpu_source=Mock();s.note=Mock()
        s.gpu_step=Mock(return_value=(step,{'markers_used':0}))
        s.accepted=Mock(return_value=True);s.odd_markers=Mock(return_value=False)
        s.shape_candidate=Mock(return_value=None)
    try:
        with patch.object(cl,'frame_points',return_value=(xyz,None,pix)),patch.object(cl,'frame_shape',return_value=(800,1280)),patch.object(cl,'isolate_mask',return_value=(np.ones(len(xyz),bool),None)):
            yield s
    finally:
        s.gpu=None;s.discard()


def test_local_recovery_quarantines_gpu_and_cpu_frames_until_confirmed():
    for cpu in [False,True]:
        with scanner(cpu) as s:
            original=s.cloud
            assert not s.feed({'t':2.})
            assert s.cue['recovery_pending'] and s.hold==cl.REFIND_HOLD
            assert not s.frame_poses()  # provisional poses must not reach saved meshing
            for i in range(cl.REFIND_HOLD):
                assert not s.feed({'t':2.03+i*.03})
                assert s.cloud is original and s.stats['merged']==3 and s.gpu_fused==0
            assert s.unconfirmed is None and not s.cue.get('recovery_pending')
            assert s.stats['recovery_confirmed']==1 and len(s.frame_poses())==cl.REFIND_HOLD+1
            if not cpu:
                assert not s.feed({'t':2.15})
                assert s.gpu_fused==1


def test_interrupted_recovery_restores_pose_and_excludes_all_provisional_frames():
    for next_t in [2.,1.9,2.8,float('nan')]:
        with scanner() as s:
            original=s.pose.copy();old_prev=s.prev
            assert not s.feed({'t':2.})
            assert not s.feed({'t':next_t})
            assert np.array_equal(s.pose,original) and s.prev is old_prev
            assert not s.stats['tracking'] and s.stats['recovery_rejected']==1
            assert not s.frame_poses() and not s.cue.get('recovery_pending')
            assert s.gpu_fused==0 and s.stats['merged']==3
    with scanner() as s:
        assert not s.feed({'t':2.})
        s.close()  # stopping during confirmation must discard it as well
        assert s.unconfirmed is None and not s.frame_poses()


def test_confirmation_loss_does_not_jump_to_another_global_hypothesis():
    with scanner() as s:
        assert not s.feed({'t':2.})
        s.accepted.return_value=False
        s.relock=Mock(return_value=True);s.refind_anywhere=Mock(return_value=True)
        assert not s.feed({'t':2.03})
        s.relock.assert_not_called();s.refind_anywhere.assert_not_called()
        assert s.unconfirmed is None and not s.stats.get('recovery_confirmed')
        assert s.gpu_fused==0


def test_anchor_bank_preserves_old_angles_when_stationary_views_repeat():
    with scanner() as s:
        points=np.c_[np.linspace(-20,20,60),np.zeros(60),np.full(60,300)]
        feat=(points,np.zeros((60,32),np.uint8))
        s.object_features=Mock(return_value=feat)
        for i in range(40):
            s.stats['merged']=i*cl.OBJECT_ANCHOR_EVERY
            s.pose=np.eye(4);s.pose[0,3]=0 if i==0 else .05
            s.remember_object_features({},i,None)
        assert len(s.feature_anchors)==2
        assert s.feature_anchors[0][0]==0 and s.feature_anchors[1][0]==1
        for i in range(40,70):
            s.stats['merged']=i*cl.OBJECT_ANCHOR_EVERY;s.pose[0,3]=i*.03
            s.remember_object_features({},i,None)
        assert len(s.feature_anchors)==cl.OBJECT_ANCHORS


def test_progress_reports_recovery_only_after_confirmation_not_first_candidate():
    progress=livescan.ScanProgress()
    progress.observe(1,1,100,False,{},now=1.)
    progress.observe(2,1,100,True,{'recovery_pending':True},now=1.03)
    state=progress.status(now=1.03)
    assert state['tracking']=='Confirming recovered position' and state['geometry']=='On hold'
    assert not state['recovered']
    progress.observe(3,1,100,True,{'recovery_confirmed':True},now=1.06)
    assert progress.status(now=1.06)['recovered']


def test_wide_searches_are_paced_one_at_a_time_and_local_relock_does_not_spend_budget():
    with scanner() as s:
        s.accepted.return_value=False;s.likeness=Mock(return_value='object')
        s.features=Mock();s.orb='keyframe';s.orb_guess=Mock(return_value=(None,None))
        s.feature_anchors.append((0,np.eye(4),None));s.keyframes=[(0,np.eye(4),None,True,None)]
        s.relock=Mock(return_value=False)
        s.refind_object_features=Mock(return_value=None)
        s.refind_anywhere=Mock(return_value=False);s.refind_shape=Mock(return_value=False)
        for t in [2.,2.03,2.21,2.24,2.42]:
            assert not s.feed({'t':t})
        assert s.refind_object_features.call_count==1
        assert s.refind_anywhere.call_count==1 and s.refind_shape.call_count==1
        assert s.stats['recovery_searches']==3 and s.gpu_fused==0
    with scanner() as s:
        s.accepted.return_value=False;s.likeness=Mock(return_value='object')
        s.relock=Mock(return_value=True)
        assert not s.feed({'t':2.})
        assert s.unconfirmed is not None and not s.stats.get('recovery_searches')


def test_real_cpu_solve_recovers_a_synthetic_view_after_depth_loss_without_early_merge():
    import tempfile
    from pathlib import Path
    from tribuutscan import calib,frames,synth
    previous=calib.CAMERA.spec
    calib.use(calib.ESTIMATES)
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rec=Path(tmp)/'recovery-rec';synth.generate(rec,n_frames=1)
            good=next(iter(frames.read_recording(rec)))
            s=cl.Scanner(track_with='joint',merge_min_s=0.,log=lambda *_:None)
            try:
                for i in range(5):s.feed(dict(good,t=.1*i))
                s.sync_merges();before=s.stats['merged'];pose=s.pose.copy()
                assert before>0
                assert not s.feed(dict(good,t=.6,depth=np.zeros_like(good['depth'])))
                assert not s.stats['tracking']
                assert not s.feed(dict(good,t=.7)) and s.unconfirmed is not None
                for i in range(cl.REFIND_HOLD):
                    assert not s.feed(dict(good,t=.8+i*.1))
                    assert s.stats['merged']==before
                assert s.stats['recovery_confirmed']==1 and s.unconfirmed is None
                delta=np.linalg.inv(pose)@s.pose
                assert np.linalg.norm(delta[:3,3])*1000<.2
                assert cl.turned(delta)[1]<.1
                for i in range(cl.KEY_EVERY):
                    s.feed(dict(good,t=1.1+i*.03))
                s.sync_merges();assert s.stats['merged']>before
            finally:s.discard()
    finally:calib.use(previous)


def test_slow_viewpoint_changes_build_separate_anchors_instead_of_sliding_one_slot():
    with scanner() as s:
        points=np.c_[np.linspace(-20,20,60),np.zeros(60),np.full(60,300)]
        s.object_features=Mock(return_value=(points,np.zeros((60,32),np.uint8)))
        for i in range(21):
            angle=np.radians(i*5);r=np.array([[np.cos(angle),0,np.sin(angle)],[0,1,0],[-np.sin(angle),0,np.cos(angle)]])
            s.pose=np.eye(4);s.pose[:3,:3]=r;s.pose[:3,3]=np.array([0,0,.3])-r@np.array([0,0,.3])
            s.stats['merged']=i*cl.OBJECT_ANCHOR_EVERY
            s.remember_object_features({},i,None)
        assert 6<=len(s.feature_anchors)<=cl.OBJECT_ANCHORS
        assert s.feature_anchors[0][0]==0
