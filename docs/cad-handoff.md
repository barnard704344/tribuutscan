# Desktop CAD handoff

Open a saved scan, finish alignment/cleanup and generate a surface in **Mesh**.
The desktop saved workspace groups the handoff under **Export**, with a link to
[ScanRuler](https://scanruler.com/?workspace=reverse) in a separate browser tab.

![Desktop CAD handoff using synthetic geometry](images/saved-cad-handoff.png)

## Download, open and model

1. In **Export**, download the STL. **Printable mesh · STL (mm)** downloads the
   existing STL when available. Otherwise **Surface mesh · STL (mm)** converts the
   current surface to `<scan-id>-surface-mm.stl`. A surface mesh is required.
2. Press **Open ScanRuler**. In that tab, load the downloaded STL and choose
   **millimetres** when asked for its units. Select **Reverse engineer** if needed.
3. Model the part against the scan, then save its editable `.scanruler` project
   and export STEP from ScanRuler. Check dimensions and compare the model with the
   scan in ScanRuler before using the CAD result.

This first handoff is a file transfer you perform in the browser. It does not
automatically load the file into the other tab, run an agent or install an MCP
helper. ScanRuler's modelling capabilities and workflow are described in its
[reverse-engineering manual](https://github.com/CNCKitchen/ScanRuler/tree/main/plugins/reverse).

## Surface and units

When no saved STL exists, the download converts saved surface coordinates from
metres to millimetres. It supports rough/open fallback surfaces without creating
a separate printable STL. An existing printable STL may contain reconstructed
regions; inspect it against the source scan before modelling critical features. Triangle geometry and
orientation are retained. It does not fill holes, close the part, create a CAD
solid or certify accuracy. Colour is not carried by this binary STL.

A point cloud alone cannot be handed off. Generate a mesh first. Cleanup/crop or
other processing may retire a mesh, so regenerate it after those edits. The
server rejects an outdated scan/mesh selection rather than serving another scan
or a previous printable STL. Downloads do not modify the capture or its exports.

## Desktop scope and saved work

The ScanRuler section is hidden on narrow screens (760 pixels or less) and devices whose
primary pointer is coarse, including phones. Scanning and the existing phone
controls and ordinary STL downloads remain available. ScanRuler runs its modelling in the desktop browser;
this feature does not move that work onto the scanner host when browsing remotely.

The project, STEP and any reports saved from ScanRuler remain on the computer
running that browser, in the locations chosen through its save/download controls.
They are not automatically attached to the Scan Library. Keep them together with
an identifiable copy of the source scan; automatic return/import is future work.

The ScanRuler link contains no scanner credentials, private host address or scan
URL. The mesh is downloaded through the existing paired model service and loaded
manually into the other application. ScanRuler is a separate project; this handoff
does not bundle or modify its code. Its own licence and any optional agent/data
settings continue to apply.
