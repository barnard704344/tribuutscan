import struct

from tribuutscan import v4l2


def test_buffer_time_reads_kernel_timestamp_and_sequence():
    b = bytearray(88)
    struct.pack_into("<qq", b, 24, 1234, 567890)  # struct timeval: seconds, microseconds
    struct.pack_into("<I", b, 56, 42)
    t, seq = v4l2.buffer_time(b)
    assert abs(t - 1234.56789) < 1e-9 and seq == 42


def test_capture_setup_unwinds_at_each_ioctl():
    from unittest.mock import Mock, patch

    stages = (v4l2.VIDIOC_S_FMT, v4l2.VIDIOC_REQBUFS, v4l2.VIDIOC_QUERYBUF,
              v4l2.VIDIOC_QBUF, v4l2.VIDIOC_STREAMON)
    for failed, maps in zip(stages, (0, 0, 0, 1, 1)):
        def ioctl(fd, request, _data):
            assert fd == 123
            if request == failed:
                raise OSError("setup failed")

        mapping = Mock()
        with patch.object(v4l2.os, "open", return_value=123), patch.object(v4l2.os, "close") as close, \
                patch.object(v4l2.fcntl, "ioctl", side_effect=ioctl), \
                patch.object(v4l2.mmap, "mmap", return_value=mapping):
            try:
                v4l2.Capture("mock-device", "YUYV", 16, 8, nbufs=1)
            except OSError as e:
                assert str(e) == "setup failed"
            else:
                raise AssertionError("setup should fail")
            close.assert_called_once_with(123)
            assert mapping.close.call_count == maps
