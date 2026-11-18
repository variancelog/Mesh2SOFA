"""Tiny worker process for the tunnel viewer's background previews.

Why a process: the real cut_and_patch spends seconds inside native pymeshfix /
PyMeshLab calls that hold the GIL, so running it in a thread freezes the viewer's
GUI for up to a couple of seconds at a time (measured: 2 s gaps on zzmeshes/genus_2).
In its own process the GUI never waits for it, and closing the viewer can simply
terminate it.

Protocol over a multiprocessing Pipe (parent = PreviewCache in
mesh_problem_viewer.py):
    parent -> child : (pts, faces)            once, at start
    parent -> child : loop index array        one per option to preview
    child  -> parent: ("ok", (new_pts, new_faces)) | ("err", message)
    parent -> child : None                    stop
Nothing here touches Qt, VTK or the disk; cut_and_patch itself is unchanged.
"""
import numpy as np


def serve(conn):
    from tunnel_loop_extractor import cut_and_patch
    try:
        pts, faces = conn.recv()
    except (EOFError, OSError):
        return
    pts = np.asarray(pts, dtype=np.float64)
    faces = np.asarray(faces)
    while True:
        try:
            loop = conn.recv()
        except (EOFError, OSError):
            return
        if loop is None:
            return
        try:
            res = cut_and_patch(pts, faces, np.asarray(loop))
            msg = ("ok", (np.asarray(res[0]), np.asarray(res[1])))
        except Exception as e:   # noqa: BLE001 - reported to the parent as the reason
            msg = ("err", str(e))
        try:
            conn.send(msg)
        except (BrokenPipeError, OSError, EOFError):
            return              # the parent went away (closed / killed): exit quietly
