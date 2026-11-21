import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

from tribuutscan import cloud, fusion, optimise, solid, view3d


def _old_exports(path):
    for name in ("mesh.ply", "mesh.stl", "solid.ply", "mesh.json"):
        (path / name).write_bytes(b"old " + name.encode())


def _get(path, endpoint):
    """Exercise the real HTTP handler logic, without opening a socket."""
    handler = view3d.Handler.__new__(view3d.Handler)
    handler.path = endpoint
    handler.source = view3d.FileSource(path)
    missing = []
    handler.send_error = lambda code: missing.append(code)
    handler.reply = lambda body, *args: (200, body)
    result = handler.do_GET()
    return result if result is not None else (missing[0], b"")


def test_remerge_retires_downloads_but_preserves_old_files():
    with TemporaryDirectory() as tmp:
        path = Path(tmp)
        _old_exports(path)
        (path / "raw.zlib").write_bytes(b"raw")
        scanner = cloud.Scanner("small")
        optimise.process(scanner, path, "medium")
        assert (path / "cloud.ply").is_file()
        assert (path / "raw.zlib").read_bytes() == b"raw"
        assert _get(path, "/mesh.stl")[0] == _get(path, "/solid.ply")[0] == 404
        assert json.loads(_get(path, "/info.json")[1])["mesh"] is None
        [old_stl] = (path / ".mesh-history").glob("*/mesh.stl")
        assert old_stl.read_bytes() == b"old mesh.stl"


def test_failed_or_rough_rebuild_does_not_serve_previous_stl():
    with TemporaryDirectory() as tmp:
        path = Path(tmp)
        scanner = cloud.Scanner("small")
        scanner.save(path)
        _old_exports(path)
        with patch.object(fusion, "fuse_scan", side_effect=ValueError("missing source")):
            assert "No mesh made" in optimise.process(scanner, path, "mesh")
        assert _get(path, "/mesh.ply")[0] == _get(path, "/mesh.stl")[0] == 404
        assert _get(path, "/solid.ply")[0] == 404

        _old_exports(path)
        angle = np.arange(32) * (2 * np.pi / 32)
        z = np.linspace(-0.94, 0.94, 16)
        phi, z = np.meshgrid(angle, z)
        r = np.sqrt(1 - z * z)
        normals = np.stack((np.cos(phi) * r, np.sin(phi) * r, z), axis=-1).reshape(-1, 3)
        pts = normals * 50
        rgb = np.full_like(pts, 200, dtype=np.uint8)
        points = SimpleNamespace(points=lambda *a, **kw: (pts, rgb, None, normals))
        with patch.object(scanner, "output_cloud", return_value=points), \
                patch.object(fusion, "fuse_scan", side_effect=ValueError("missing source")):
            assert "PLY only" in optimise.process(scanner, path, "mesh")
        assert _get(path, "/mesh.ply")[0] == _get(path, "/mesh.bin")[0] == 200
        assert _get(path, "/mesh.stl")[0] == _get(path, "/solid.ply")[0] == 404
        info = json.loads(_get(path, "/info.json")[1])
        assert info["mesh"] and info["stl"] is None and info["solid"] is None


def test_rebuilt_closed_and_open_mesh_downloads_match_current_report():
    with TemporaryDirectory() as tmp:
        path = Path(tmp)
        scanner = cloud.Scanner("small")
        scanner.save(path)
        xyz = np.array([[0, 0, 0], [100, 0, 0], [0, 100, 0], [0, 0, 100]], dtype=float)
        tri = np.array([[0, 2, 1], [0, 1, 3], [0, 3, 2], [1, 2, 3]])
        rgb = np.full((4, 3), 200, dtype=np.uint8)
        with patch.object(fusion, "fuse_scan", side_effect=lambda *a, **kw: (xyz, tri, rgb, {})), \
                patch.object(solid, "close", return_value=(xyz, tri, rgb, {"edges_closed": True})):
            fusion.make_mesh(path, scanner=scanner)
            assert _get(path, "/mesh.stl")[0] == _get(path, "/solid.ply")[0] == 200
            assert json.loads(_get(path, "/info.json")[1])["stl_state"] == "closed"
            fusion.make_mesh(path, scanner=scanner, close=False)
            assert _get(path, "/mesh.stl")[0] == 200 and _get(path, "/solid.ply")[0] == 404
            assert json.loads(_get(path, "/info.json")[1])["stl_state"] == "open"
            # An STL writer that fails must not leave stale or half-built files active.
            with patch.object(fusion.o3d.io, "write_triangle_mesh", return_value=False):
                try:
                    fusion.make_mesh(path, scanner=scanner, close=False)
                except OSError as e:
                    assert "write the STL" in str(e)
                else:
                    raise AssertionError("STL writer failure was hidden")
            assert _get(path, "/mesh.stl")[0] == _get(path, "/mesh.ply")[0] == 404
