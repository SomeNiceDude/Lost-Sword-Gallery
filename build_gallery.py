#!/usr/bin/env python3
"""
Build a local HTML gallery for browsing Spine characters & assets.

Recursively scans an entire Unity Assets tree (e.g. an AssetStudio export of
the LSDM game files) and produces an interactive gallery showing every folder
that contains viewable content (Spine skel+atlas+png sets, loose textures,
audio, video, etc.).

Generates:
  - index.html          Main gallery (uses pre-generated thumb.png if available)
  - start_server.bat    Serves the gallery on port 8080
  - generate_thumbs.py  Server for batch thumbnail generation (port 8081)
  - generate_thumbs.bat Shortcut to start thumbnail generator
  - generate_thumbs.html Page that renders each character and saves thumbnails

Workflow:
  1. python build_gallery.py "C:/Users/denni/Desktop/LSDM/Extracted/Assets"
  2. Double-click generate_thumbs.bat  (one-time, generates thumb.png per character)
  3. Double-click start_server.bat     (browse the gallery)

Usage:
  python build_gallery.py <path_to_Assets_folder>
"""

import json
import html as html_mod
import os
import sys
import urllib.request
import urllib.error
from pathlib import Path

SPINE_PLAYER_URLS = [
    "https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/iife/spine-player.js",
    "https://cdn.jsdelivr.net/npm/@esotericsoftware/spine-player@4.1.23/dist/iife/spine-player.js",
]
SPINE_CSS_URLS = [
    "https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/spine-player.css",
    "https://cdn.jsdelivr.net/npm/@esotericsoftware/spine-player@4.1.23/dist/spine-player.css",
]
EXTRA_LIBS = {
    "lib/jszip.min.js": "https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js",
    "lib/gif.js": "https://cdn.jsdelivr.net/npm/gif.js@0.2.0/dist/gif.js",
    "lib/gif.worker.js": "https://cdn.jsdelivr.net/npm/gif.js@0.2.0/dist/gif.worker.js",
}


def download_file(url, dest: Path) -> bool:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = resp.read()
            if len(data) < 1000:
                return False
            if b'<!DOCTYPE' in data or b'<html' in data[:200]:
                return False
            dest.write_bytes(data)
            return True
    except Exception as e:
        print(f"    Failed: {url} ({e})")
        return False


def download_spine_runtime(dest_dir: Path) -> bool:
    spine_dir = dest_dir / "spine"
    spine_dir.mkdir(exist_ok=True)
    js_file = spine_dir / "spine-player.js"
    css_file = spine_dir / "spine-player.css"
    if js_file.exists(): js_file.unlink()
    if css_file.exists(): css_file.unlink()
    print("  Downloading spine-player runtime...")
    for url in SPINE_PLAYER_URLS:
        if download_file(url, js_file):
            print(f"    OK: {js_file.name} ({js_file.stat().st_size // 1024} KB)")
            break
    else:
        print("    Could not download spine-player.js")
        return False
    for url in SPINE_CSS_URLS:
        if download_file(url, css_file):
            print(f"    OK: {css_file.name}")
            break
    return True


def download_extra_libs(dest_dir: Path):
    lib_dir = dest_dir / "lib"
    lib_dir.mkdir(exist_ok=True)
    print("  Downloading export libraries...")
    for fname, url in EXTRA_LIBS.items():
        dest = lib_dir / fname.split("/")[-1]
        if dest.exists(): dest.unlink()
        if download_file(url, dest):
            print(f"    OK: {dest.name}")
        else:
            print(f"    Skipped: {fname}")


def scan_characters(root: Path):
    """Recursively walk the entire Assets tree and emit one gallery entry per
    leaf-content folder.

    The Unity-native folder structure looks like:
        Assets/
          AssetAddressable/
            Prefabs/
              LobbyUnit/<Name>/   <- 148 character folders
              Pet/<Pet_Name>/     <- 64 pet folders
              Unit/<Unit_Name>/   <- 167 unit folders (enemies, NPCs, etc.)
              Map/<Map_Name>/     <- 65 map background folders
            Atlas/                <- equipment & weapon atlases
            Texture/             <- loose textures (Quest, Shop subfolders)
            Sound/
          Texture2D/             <- loose textures
          TextAsset/             <- text data files
          Resources/             <- game resources (atlas, prefabs, etc.)
          Font/, Mesh/, Shader/, spine/   <- other Unity asset folders

    Each leaf folder that directly contains viewable files (image/audio/video/
    skel/atlas/json/obj) becomes ONE gallery entry. The entry's `name` is the
    full relative path from `root` (e.g. 'AssetAddressable/Prefabs/LobbyUnit/
    Agravaine') so it's unique even when the same character name appears in
    multiple categories (Agravaine exists in LobbyUnit, Unit, AND Pet).

    `display_name` is the last path segment (e.g. 'Agravaine') for card titles.
    `category` is a short label (e.g. 'LobbyUnit', 'Pet', 'Texture2D') used by
    the category filter dropdown.

    `dir` and `thumb_dir` are the full relative path so URL construction works
    regardless of nesting depth.
    """
    AUDIO_EXT = {".wav", ".mp3", ".ogg", ".flac"}
    VIDEO_EXT = {".mp4", ".webm", ".avi", ".mkv"}
    IMG_EXT = {".png", ".jpg", ".jpeg", ".webp"}

    # Unity-internal top-level folders with no visual content. Skipping these
    # keeps the gallery focused on actual game assets.
    SKIP_TOP_DIRS = {
        "BuildSettings", "EditorBuildSettings", "EditorSettings",
        "GraphicsSettings", "InputManager", "MonoManager",
        "NavMeshProjectSettings", "Physics2DSettings", "PhysicsManager",
        "PhysicsMaterial2D", "PlayerSettings", "QualitySettings",
        "ResourceManager", "RuntimeInitializeOnLoadManager",
        "ShaderNameRegistry", "AudioManager", "DelayedCallManager",
        "AssetBundle", "PrefabHierarchyObject", "SceneHierarchyObject",
        "lib",
    }

    def scan_folder_files(folder_path: Path, rel_dir: str):
        """Scan one folder on disk, returning a list of file dicts. Each file
        dict carries its source folder (relative to root, forward slashes) via
        `dir` so the gallery can build the correct URL even for deeply nested
        folders."""
        out = []
        try:
            entries = sorted(folder_path.iterdir())
        except (PermissionError, OSError):
            return out
        for f in entries:
            if not f.is_file():
                continue
            if f.name == 'thumb.png':
                continue
            if f.name.startswith('.'):
                continue
            fn_lower = f.name.lower()
            # Skip Unity .meta files (per-asset metadata, never useful in gallery)
            if fn_lower.endswith('.meta'):
                continue
            # Skip Unity .cs / .dll / .exe (code, not viewable)
            if fn_lower.endswith(('.cs', '.dll', '.exe', '.so', '.dylib')):
                continue
            try:
                fsize = f.stat().st_size
            except OSError:
                continue
            # Detect .skel / .atlas by substring match (suffix doesn't work for
            # files like "Ran.skel #449544" where Path.suffix returns
            # ".skel #449544" instead of ".skel").
            # Also handle Unity-extracted ".atlas.bytes" / ".skel.bytes".
            is_skel = (fn_lower.endswith('.skel') or '.skel ' in fn_lower
                       or fn_lower.endswith('.skel.bin')
                       or fn_lower.endswith('.skel.bytes')
                       or '.skel.bytes ' in fn_lower)
            is_atlas = (fn_lower.endswith('.atlas') or '.atlas ' in fn_lower
                        or fn_lower.endswith('.atlas.bytes')
                        or '.atlas.bytes ' in fn_lower)
            if is_skel: ext = '.skel'
            elif is_atlas: ext = '.atlas'
            else: ext = f.suffix.lower()
            out.append({"name": f.name, "ext": ext, "size": fsize, "dir": rel_dir})
        return out

    def derive_category(path_parts):
        """Extract a short category label from the relative path."""
        if not path_parts:
            return '(root)'
        # AssetAddressable/Prefabs/<Category>/<Name> -> Category
        if (len(path_parts) >= 4 and path_parts[0] == 'AssetAddressable'
                and path_parts[1] == 'Prefabs'):
            return path_parts[2]
        # AssetAddressable/<Sub>/<...> -> Sub (Atlas, Texture, Sound, etc.)
        if len(path_parts) >= 2 and path_parts[0] == 'AssetAddressable':
            return path_parts[1]
        # Resources/<Sub>/<...> -> Resources/<Sub>
        if len(path_parts) >= 2 and path_parts[0] == 'Resources':
            return 'Resources/' + path_parts[1]
        # Otherwise: top-level folder name
        return path_parts[0]

    def build_character(name, display_name, category, rel_dir, files):
        """Assemble the character dict.
        `name` is the full relative path (unique identifier).
        `display_name` is the last path segment (card title).
        `category` is a short label for the filter dropdown.
        `rel_dir` is the relative path (where thumb.png lives)."""
        total_size = sum(f["size"] for f in files)
        audio_files = [f for f in files if f["ext"] in AUDIO_EXT]
        video_files = [f for f in files if f["ext"] in VIDEO_EXT]
        image_files = [f for f in files if f["ext"] in IMG_EXT]

        # Thumb = smallest-named image (NOT thumb.png, which is excluded above).
        thumb = None
        for f in image_files:
            if thumb is None or len(f["name"]) < len(thumb["name"]):
                thumb = f

        # Sort candidates: prefer files WITHOUT '#' in the name first
        # (these are usually the "primary" extraction). The '#' suffixed
        # files are alternate versions / duplicates from Unity AssetStudio.
        skel_candidates = sorted(
            [f for f in files if f["ext"] == ".skel"],
            key=lambda f: ('#' in f["name"], f["name"])
        )
        atlas_candidates = sorted(
            [f for f in files if f["ext"] == ".atlas"],
            key=lambda f: ('#' in f["name"], f["name"])
        )
        skel_file = skel_candidates[0] if skel_candidates else None
        atlas_file = atlas_candidates[0] if atlas_candidates else None

        json_skel = None
        if skel_file is None:
            json_cands = sorted(
                [f for f in files if f["ext"] == ".json" and "SkeletonData" in f["name"]],
                key=lambda f: ('#' in f["name"], f["name"])
            )
            if json_cands:
                json_skel = json_cands[0]

        has_thumb = (root / rel_dir / 'thumb.png').exists()

        has_media = (
            len(image_files) > 0
            or len(audio_files) > 0
            or len(video_files) > 0
        )
        return {
            "name": name,
            "display_name": display_name,
            "category": category,
            "files": files,
            "file_count": len(files),
            "total_size": total_size,
            "thumb": thumb,
            "skel": skel_file,
            "atlas": atlas_file,
            "skel_candidates": skel_candidates,
            "atlas_candidates": atlas_candidates,
            "json_skel": json_skel,
            "has_skel": skel_file is not None or json_skel is not None,
            "has_atlas": atlas_file is not None,
            "has_json": any(f["ext"] == ".json" for f in files),
            "has_obj": any(f["ext"] == ".obj" for f in files),
            "has_audio": len(audio_files) > 0,
            "has_video": len(video_files) > 0,
            "can_play": (skel_file is not None or json_skel is not None) and atlas_file is not None,
            "has_thumb": has_thumb,
            "has_media": has_media,
            "thumb_dir": rel_dir,
        }

    characters = []
    # os.walk with topdown=True lets us prune skipped dirs via dirnames[:] = ...
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune hidden dirs and Unity-internal top-level dirs.
        # Only prune at the TOP level (when dirpath == root) so we don't
        # accidentally skip a subfolder that happens to share a name with
        # a Unity-internal folder.
        if os.path.abspath(dirpath) == os.path.abspath(root):
            dirnames[:] = sorted(
                d for d in dirnames
                if not d.startswith('.') and d not in SKIP_TOP_DIRS
            )
        else:
            dirnames[:] = sorted(d for d in dirnames if not d.startswith('.'))

        # Compute relative path from root, using forward slashes for URLs.
        rel = os.path.relpath(dirpath, root).replace(os.sep, '/')
        if rel == '.':
            # root itself: just continue walking into subdirs
            continue

        path_parts = rel.split('/')
        # If the top-level dir is in SKIP_TOP_DIRS (e.g., we somehow entered
        # it), prune its subdirs and skip.
        if path_parts[0] in SKIP_TOP_DIRS:
            dirnames[:] = []
            continue

        # Scan this folder's direct files.
        files = scan_folder_files(Path(dirpath), rel)
        if not files:
            # No viewable files in this folder; only subfolders. Don't emit
            # an entry -- the subfolders will be emitted when walked.
            continue

        display_name = path_parts[-1] if path_parts else rel
        category = derive_category(path_parts)
        characters.append(build_character(rel, display_name, category, rel, files))

    # Sort by category then display_name for stable, grouped ordering.
    characters.sort(key=lambda c: (c["category"].lower(), c["display_name"].lower()))
    return characters


def build_html(characters, root_name, has_spine_local):
    root_name_safe = html_mod.escape(root_name)
    chars_json = json.dumps(characters, ensure_ascii=False)

    # Always load Spine from CDN. If `has_spine_local` is True, also wire up a
    # synchronous fallback to the local spine/ folder (for offline use).
    # The fallback uses document.write('<scr'+'ipt ...>') -- split tag so the
    # HTML parser doesn't terminate the outer script early.
    #
    # IMPORTANT: use Spine 4.1 (not 4.2). LSDM assets are Spine 4.1 format,
    # and 4.2's runtime requires physics to be passed to updateWorldTransform(),
    # which breaks our captureAllFrames() export path.
    #
    # JSZip + gif.js are loaded from CDN with local fallback -- they're needed
    # for the "PNG Sequence" and "Save GIF" export buttons.
    if has_spine_local:
        spine_tags = (
            '<link rel="stylesheet" href="https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/spine-player.css" onerror="this.href=\'spine/spine-player.css\'">\n'
            '<script src="https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/iife/spine-player.js"></script>\n'
            '<script>if(typeof spine==="undefined"){document.write(\'<scr\'+\'ipt src="spine/spine-player.js"><\\/scr\'+\'ipt>\');}</script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>\n'
            '<script>if(typeof JSZip==="undefined"){document.write(\'<scr\'+\'ipt src="lib/jszip.min.js"><\\/scr\'+\'ipt>\');}</script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/gif.js@0.2.0/dist/gif.js"></script>\n'
            '<script>if(typeof GIF==="undefined"){document.write(\'<scr\'+\'ipt src="lib/gif.js"><\\/scr\'+\'ipt>\');}</script>'
        )
    else:
        # CDN only -- works on github.io without needing local spine/ folder
        spine_tags = (
            '<link rel="stylesheet" href="https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/spine-player.css">\n'
            '<script src="https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/iife/spine-player.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/@esotericsoftware/spine-player@4.1.23/dist/iife/spine-player.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/jszip@3.10.1/dist/jszip.min.js"></script>\n'
            '<script src="https://cdn.jsdelivr.net/npm/gif.js@0.2.0/dist/gif.js"></script>'
        )

    js_code = 'const CHARS = ' + chars_json + ';' + r'''

const ROOT = "./";
let filtered = [...CHARS];
let curFilter = 'all', curSort = 'name-asc', curSearch = '', curCategory = '';
let curIdx = -1, curPlayer = null;
let exporting = false;
let zoomPanCleanup = null;

const $ = id => document.getElementById(id);
const grid=$('grid'), empty=$('empty'), loading=$('loading'), searchEl=$('search'), statsEl=$('stats');

function fmtSize(b) { return b<1024?b+' B':b<1048576?(b/1024).toFixed(1)+' KB':(b/1048576).toFixed(1)+' MB'; }

/* Encode a forward-slash-separated relative path for use in a URL.
   `encodeURIComponent` alone would turn 'a/b/c' into 'a%2Fb%2Fc' which the
   static HTTP server treats as a single path segment (404). We split on '/',
   encode each segment, then rejoin with '/' so the server sees the nested
   path correctly. The FILENAME (which may contain '#NNNNN' suffixes) is
   always encoded with encodeURIComponent separately. */
function encodePath(p) {
  if (!p) return '';
  return String(p).split('/').map(encodeURIComponent).join('/');
}
/* Build a URL for a file in a (possibly nested) dir. */
function fileUrl(dir, name) {
  return ROOT + encodePath(dir) + '/' + encodeURIComponent(name);
}

// --- Filtering ---
function applyF() {
  var q = curSearch.toLowerCase();
  filtered = CHARS.filter(c => {
    /* Search matches display_name OR full path OR category. This lets users
       type "Agravaine" to find all Agravaine variants across LobbyUnit/Unit/
       Pet, OR type "Pet" to find all pet entries. */
    if (q) {
      var hay = (c.display_name || c.name).toLowerCase() + ' ' + c.name.toLowerCase() + ' ' + (c.category || '').toLowerCase();
      if (hay.indexOf(q) === -1) return false;
    }
    /* Category filter: when set, only show entries in that category. */
    if (curCategory && c.category !== curCategory) return false;
    /* 'all' = character folders only (has actual media content).
       Non-character folders (JSON-only, config dumps, etc.) are hidden
       by default. Use 'allfolders' to see everything. */
    if (curFilter === 'all') return c.has_media;
    if (curFilter === 'allfolders') return true;
    if (curFilter === 'playable') return c.can_play;
    if (curFilter === 'skel') return c.has_skel;
    if (curFilter === 'noimg') return !c.thumb;
    if (curFilter === 'media') return c.has_audio || c.has_video;
    return true;
  });
  applyS();
}

function applyS() {
  var parts = curSort.split('-');
  var k = parts[0], d = parts[1];
  filtered.sort((a, b) => {
    var va, vb;
    if (k === 'name') {
      /* Sort by display_name (not full path) so all "Agravaine" variants
         appear together regardless of category prefix. Ties broken by
         full path so order is stable. */
      va = (a.display_name || a.name).toLowerCase() + '||' + a.name.toLowerCase();
      vb = (b.display_name || b.name).toLowerCase() + '||' + b.name.toLowerCase();
    }
    else if (k === 'files') { va = a.file_count; vb = b.file_count; }
    else { va = a.total_size; vb = b.total_size; }
    return va < vb ? (d === 'asc' ? -1 : 1) : va > vb ? (d === 'asc' ? 1 : -1) : 0;
  });
  render();
}

function render() {
  loading.style.display = 'none';
  var mediaCount = CHARS.filter(c => c.has_media).length;
  var nonMediaCount = CHARS.length - mediaCount;
  var totalVisible = CHARS.length;
  /* When viewing the default 'Characters' filter, show how many non-media
     folders are hidden. For other filters, just show counts. */
  var statsText = 'Showing ' + filtered.length + ' of ' + totalVisible + ' entries';
  if (curFilter === 'all' && nonMediaCount > 0) {
    statsText += ' &middot; ' + nonMediaCount + ' non-media folder' + (nonMediaCount === 1 ? '' : 's') + ' filtered';
  }
  statsEl.innerHTML = statsText;
  $('tc').textContent = mediaCount + ' characters';
  if (!filtered.length) { grid.style.display = 'none'; empty.style.display = 'block'; return; }
  grid.style.display = 'grid'; empty.style.display = 'none';
  var frag = document.createDocumentFragment();
  for (var ci = 0; ci < filtered.length; ci++) {
    var c = filtered[ci];
    var card = document.createElement('div');
    card.className = 'card';
    var idx = CHARS.indexOf(c);
    (function(card, c, idx) {
      card.onclick = function() { openM(idx); };
    })(card, c, idx);
    var th;
    /* Always attempt to load thumb.png for playable characters OR characters
       flagged as having a thumb. The has_thumb flag is baked at build_gallery.py
       time, but thumbs are generated later by generate_thumbs.bat -- so we
       can't trust has_thumb=false. The onerror handler swaps to a fallback
       (first image file, or '?') if thumb.png doesn't exist.

       thumb.png lives in the character's `thumb_dir` folder (= c.name, the
       full relative path from the Assets root). */
    var thumbDir = c.thumb_dir || c.name;
    if (c.has_thumb || c.can_play) {
      var fallback = c.thumb
        ? fileUrl(c.thumb.dir || thumbDir, c.thumb.name)
        : '';
      th = '<img src="' + ROOT + encodePath(thumbDir) + '/thumb.png" alt="" loading="lazy" onerror="' +
           (fallback
             ? 'this.onerror=null;this.src=\'' + fallback + '\';'
             : 'this.style.display=\'none\';') + '">';
    } else {
      th = c.thumb ? '<img src="' + fileUrl(c.thumb.dir || thumbDir, c.thumb.name) + '" alt="" loading="lazy">' : '<span class="noimg">?</span>';
    }
    var bd = '';
    if (c.has_skel) bd += '<span class="b b-s">skel</span>';
    if (c.has_atlas) bd += '<span class="b b-a">atlas</span>';
    if (c.has_json) bd += '<span class="b b-j">json</span>';
    if (c.has_obj) bd += '<span class="b b-o">obj</span>';
    if (c.has_audio) bd += '<span class="b b-w">audio</span>';
    if (c.has_video) bd += '<span class="b b-v">video</span>';
    var pi = c.can_play ? '<div class="cplay">&#9654;</div>' : '';
    /* Card title shows display_name (last path segment, e.g. 'Agravaine').
       A small category label sits above it (e.g. 'LobbyUnit', 'Pet').
       The full path is in the title attribute for tooltip hover. */
    var dispName = c.display_name || c.name;
    var catLabel = c.category || '';
    var catHtml = catLabel ? '<div class="ccat">' + catLabel + '</div>' : '';
    card.innerHTML = '<div class="cthumb">' + th + '<div class="bdg">' + bd + '</div>' + pi + '</div><div class="cinfo">' + catHtml + '<div class="cname" title="' + c.name + '">' + dispName + '</div><div class="cmeta">' + c.file_count + ' files &middot; ' + fmtSize(c.total_size) + '</div></div>';
    frag.appendChild(card);
  }
  grid.innerHTML = ''; grid.appendChild(frag);
}

// --- Modal Player ---
function destroyPlayer() {
  cleanupZoomPan();
  if (curPlayer) { try { curPlayer.dispose(); } catch(e){} curPlayer = null; }
  $('player-container').innerHTML = '';
  $('player-error').style.display = 'none';
  $('skin-select-wrap').style.display = 'none';
}

// --- High-quality render: boost WebGL buffer resolution ---
// SpinePlayer sizes the canvas drawing buffer to clientWidth * devicePixelRatio
// and sets camera.setViewport(canvas.width, canvas.height) -- so the projection
// matches the canvas 1:1. Every frame, drawFrame recomputes
//   camera.zoom = viewport.width / this.canvas.width
// so the visible world always equals the skeleton bounds.
//
// To supersample, we make the canvas RENDER_SCALE x bigger AND scale the
// camera viewport by the same factor. The projection is now for the larger
// viewport, the GL viewport fills the larger canvas, and the canvas is then
// CSS-scaled back down to the display size. Net effect: 4x more pixels at
// RENDER_SCALE=2, with framing preserved.
//
// PREVIOUS BUG: we used to set camera.setViewport(camW, camH) (= unscaled
// dimensions) while keeping canvas at RENDER_SCALE x. Because drawFrame uses
// this.canvas.width for the zoom denominator -- not camera.viewportWidth --
// the computed zoom was too small, and the visible world came out at HALF
// the skeleton bounds. Characters appeared cropped at the edges even when
// the CSS zoom/pan was scaled out, because the canvas itself only contained
// the middle 50% of the character.
var RENDER_SCALE = 2; // 2x in each dim = 4x pixels. Set to 1 to disable.
function setupHighQualityRender(player) {
  if (!player || !player.sceneRenderer) return;
  var sr = player.sceneRenderer;
  if (sr._hqPatched) return; // only patch once per renderer
  sr._hqPatched = true;
  var origResize = sr.resize.bind(sr);
  sr.resize = function(mode) {
    var canvas = sr.canvas;
    var dpr = window.devicePixelRatio || 1;
    // Drawing buffer at RENDER_SCALE x the CSS pixel size.
    var w = Math.round(canvas.clientWidth * dpr * RENDER_SCALE);
    var h = Math.round(canvas.clientHeight * dpr * RENDER_SCALE);
    if (canvas.width != w || canvas.height != h) {
      canvas.width = w;
      canvas.height = h;
    }
    sr.context.gl.viewport(0, 0, canvas.width, canvas.height);
    // Camera viewport MUST match canvas.width/height so the projection
    // matrix matches what drawFrame's zoom computation expects.
    // (drawFrame uses this.canvas.width as the zoom denominator.)
    sr.camera.setViewport(canvas.width, canvas.height);
    sr.camera.update();
  };
}

// --- Zoom & Pan via CSS transforms (safe: no camera/control interference) ---
var zoomPanState = { scale: 1, tx: 0, ty: 0 };
function setupZoomPan(player) {
  cleanupZoomPan();
  var renderer = player.sceneRenderer;
  if (!renderer || !renderer.canvas) return;
  var canvas = renderer.canvas;
  var wrap = $('player-wrap');
  var scale = 1, tx = 0, ty = 0;
  zoomPanState.scale = 1; zoomPanState.tx = 0; zoomPanState.ty = 0;
  var isPanning = false, lastX = 0, lastY = 0;
  canvas.style.cursor = 'grab';

  function apply() {
    canvas.style.transformOrigin = 'center center';
    canvas.style.transform = 'translate(' + tx + 'px,' + ty + 'px) scale(' + scale + ')';
    zoomPanState.scale = scale; zoomPanState.tx = tx; zoomPanState.ty = ty;
  }
  function onWheel(e) {
    if (e.target.closest('.spine-player-controls')) return;
    e.preventDefault();
    var rect = canvas.getBoundingClientRect();
    var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
    var mx = e.clientX - cx, my = e.clientY - cy;
    var oldScale = scale;
    var d = e.deltaY;
    if (e.deltaMode === 1) d *= 8;
    if (e.deltaMode === 2) d *= 24;
    scale *= d > 0 ? 0.92 : 1.08;
    scale = Math.max(0.1, Math.min(10, scale));
    tx = mx - (mx - tx) * (scale / oldScale);
    ty = my - (my - ty) * (scale / oldScale);
    apply();
  }
  function onDown(e) {
    if (e.button !== 0) return;
    if (e.target !== canvas && !e.target.closest('#player-wrap')) return;
    isPanning = true; lastX = e.clientX; lastY = e.clientY;
    canvas.style.cursor = 'grabbing';
    e.preventDefault();
  }
  function onMove(e) {
    if (!isPanning) return;
    tx += e.clientX - lastX; ty += e.clientY - lastY;
    lastX = e.clientX; lastY = e.clientY;
    apply();
  }
  function onUp() { if (isPanning) { isPanning = false; canvas.style.cursor = 'grab'; } }
  function onDblClick(e) { if (e.target === canvas || e.target.closest('#player-wrap')) { scale = 1; tx = 0; ty = 0; apply(); } }

  // --- Touch: one-finger pan, two-finger pinch-to-zoom ---
  var lastTouches = [];
  function onTouchStart(e) {
    if (e.target.closest('.spine-player-controls')) return;
    lastTouches = Array.from(e.touches);
    if (e.touches.length === 1) {
      e.preventDefault();
    }
  }
  function onTouchMove(e) {
    if (e.target.closest('.spine-player-controls')) return;
    if (e.touches.length === 1 && lastTouches.length === 1) {
      e.preventDefault();
      var dx = e.touches[0].clientX - lastTouches[0].clientX;
      var dy = e.touches[0].clientY - lastTouches[0].clientY;
      tx += dx; ty += dy;
      lastTouches = Array.from(e.touches);
      apply();
    } else if (e.touches.length === 2 && lastTouches.length === 2) {
      e.preventDefault();
      var prevDist = Math.hypot(lastTouches[0].clientX - lastTouches[1].clientX, lastTouches[0].clientY - lastTouches[1].clientY);
      var currDist = Math.hypot(e.touches[0].clientX - e.touches[1].clientX, e.touches[0].clientY - e.touches[1].clientY);
      if (prevDist > 0) {
        var rect = canvas.getBoundingClientRect();
        var cx = rect.left + rect.width / 2, cy = rect.top + rect.height / 2;
        var mx = (e.touches[0].clientX + e.touches[1].clientX) / 2 - cx;
        var my = (e.touches[0].clientY + e.touches[1].clientY) / 2 - cy;
        var oldScale = scale;
        scale *= currDist / prevDist;
        scale = Math.max(0.1, Math.min(10, scale));
        tx = mx - (mx - tx) * (scale / oldScale);
        ty = my - (my - ty) * (scale / oldScale);
        lastTouches = Array.from(e.touches);
        apply();
      }
    }
  }
  function onTouchEnd(e) {
    lastTouches = Array.from(e.touches);
  }

  wrap.addEventListener('wheel', onWheel, { passive: false });
  canvas.addEventListener('mousedown', onDown);
  document.addEventListener('mousemove', onMove);
  document.addEventListener('mouseup', onUp);
  canvas.addEventListener('dblclick', onDblClick);
  wrap.addEventListener('touchstart', onTouchStart, { passive: false });
  wrap.addEventListener('touchmove', onTouchMove, { passive: false });
  wrap.addEventListener('touchend', onTouchEnd);
  zoomPanCleanup = function() {
    wrap.removeEventListener('wheel', onWheel);
    canvas.removeEventListener('mousedown', onDown);
    document.removeEventListener('mousemove', onMove);
    document.removeEventListener('mouseup', onUp);
    canvas.removeEventListener('dblclick', onDblClick);
    wrap.removeEventListener('touchstart', onTouchStart);
    wrap.removeEventListener('touchmove', onTouchMove);
    wrap.removeEventListener('touchend', onTouchEnd);
    canvas.style.transform = '';
    canvas.style.transformOrigin = '';
    canvas.style.cursor = '';
  };
}

// --- Mobile zoom button helpers ---
function mobileZoomIn() {
  if (!curPlayer || !curPlayer.sceneRenderer) return;
  var canvas = curPlayer.sceneRenderer.canvas;
  var wrap = $('player-wrap');
  if (!wrap) return;
  var s = zoomPanState;
  var oldScale = s.scale;
  s.scale *= 1.25;
  s.scale = Math.max(0.1, Math.min(10, s.scale));
  canvas.style.transformOrigin = 'center center';
  canvas.style.transform = 'translate(' + s.tx + 'px,' + s.ty + 'px) scale(' + s.scale + ')';
}
function mobileZoomOut() {
  if (!curPlayer || !curPlayer.sceneRenderer) return;
  var canvas = curPlayer.sceneRenderer.canvas;
  var s = zoomPanState;
  s.scale *= 0.8;
  s.scale = Math.max(0.1, Math.min(10, s.scale));
  canvas.style.transformOrigin = 'center center';
  canvas.style.transform = 'translate(' + s.tx + 'px,' + s.ty + 'px) scale(' + s.scale + ')';
}
function mobileZoomReset() {
  if (!curPlayer || !curPlayer.sceneRenderer) return;
  var canvas = curPlayer.sceneRenderer.canvas;
  zoomPanState.scale = 1; zoomPanState.tx = 0; zoomPanState.ty = 0;
  canvas.style.transform = '';
  canvas.style.transformOrigin = '';
}

function cleanupZoomPan() {
  if (zoomPanCleanup) { zoomPanCleanup(); zoomPanCleanup = null; }
}

function autoSelectBestSkin(player) {
  try {
    var skeleton = player.skeleton;
    if (!skeleton || !skeleton.data || !skeleton.data.skins) return;
    var skins = skeleton.data.skins;
    if (skins.length <= 1) return;
    /* Pick the skin with the most SETUP attachments (i.e. attachments
       that are visible when the slot is in its setup pose).
       Previous code used slot.name (the slot's name like "head") which is
       wrong -- Spine's Skin.getAttachment(slotIndex, name) expects the
       ATTACHMENT name (like "head-default"), not the slot name. This
       caused the count to be 0 for all skins, picking the wrong one.
       Also prefer skins literally named "default" / "Default" since
       artists conventionally use that name for the intended base skin. */
    var bestSkin = null, bestCount = -1;
    var defaultSkin = null;
    for (var i = 0; i < skins.length; i++) {
      var skin = skins[i];
      var nm = (skin.name || '').toLowerCase();
      if (nm === 'default' || nm === 'base' || nm === 'normal') defaultSkin = skin.name;
      var count = 0;
      for (var j = 0; j < skeleton.data.slots.length; j++) {
        var slot = skeleton.data.slots[j];
        /* Use attachmentName (the setup attachment name), not slot.name */
        if (slot.attachmentName && skin.getAttachment(slot.index, slot.attachmentName)) count++;
      }
      if (count > bestCount) { bestCount = count; bestSkin = skin.name; }
    }
    /* Prefer the explicitly-named "default" skin when it has a reasonable
       number of attachments (>= 50% of the best). This catches characters
       where the artist named the intended base skin "default" but there's
       another skin with more swap attachments that isn't the intended view. */
    if (defaultSkin && bestCount > 0) {
      var defaultCount = 0;
      var defSkin = skins.find(function(s){return s.name === defaultSkin;});
      if (defSkin) {
        for (var k = 0; k < skeleton.data.slots.length; k++) {
          var sl = skeleton.data.slots[k];
          if (sl.attachmentName && defSkin.getAttachment(sl.index, sl.attachmentName)) defaultCount++;
        }
        if (defaultCount >= bestCount * 0.5) bestSkin = defaultSkin;
      }
    }
    if (bestSkin && bestSkin !== skeleton.skin.name) {
      skeleton.setSkinByName(bestSkin);
      skeleton.setSlotsToSetupPose();
    }
    populateSkinDropdown(player, bestSkin);
  } catch(e) { console.warn('autoSelectSkin error:', e); }
}

function populateSkinDropdown(player, currentSkinName) {
  try {
    var skeleton = player.skeleton;
    if (!skeleton || !skeleton.data || !skeleton.data.skins) return;
    var skins = skeleton.data.skins;
    if (skins.length <= 1) { $('skin-select-wrap').style.display = 'none'; return; }
    var sel = $('skin-select');
    sel.innerHTML = '';
    for (var i = 0; i < skins.length; i++) {
      var opt = document.createElement('option');
      opt.value = skins[i].name;
      opt.textContent = skins[i].name;
      if (skins[i].name === currentSkinName) opt.selected = true;
      sel.appendChild(opt);
    }
    $('skin-select-wrap').style.display = 'flex';
  } catch(e) {}
}

function onSkinChange() {
  if (!curPlayer || !curPlayer.skeleton) return;
  try {
    var skinName = $('skin-select').value;
    curPlayer.skeleton.setSkinByName(skinName);
    curPlayer.skeleton.setSlotsToSetupPose();
  } catch(e) { console.warn('skin change error:', e); }
}

function findJsonSkel(c) {
  var js = c.files.find(function(f){return f.ext==='.json'&&f.name.indexOf('SkeletonData')!==-1&&f.name.indexOf('#')===-1;});
  return js || c.files.find(function(f){return f.ext==='.json'&&f.name.indexOf('SkeletonData')!==-1;});
}

function loadSpineModel(c) {
  destroyPlayer();
  var container = $('player-container'), errEl = $('player-error');
  if (!c.can_play) {
    errEl.innerHTML = 'No Spine skeleton data for this character. Check the Images or Files tabs.';
    errEl.style.display = 'block';
    return;
  }
  errEl.style.display = 'none';
  container.innerHTML = '<div style="text-align:center;padding:80px;color:#666"><div class="spinner"></div><br>Loading model...</div>';

  /* Build the list of (skel, atlas) combinations to try.
     Characters extracted from Unity bundles may have multiple versions of
     the .skel and .atlas files (with '#NNNNN' suffixes from AssetStudio).
     The primary pair (skel, atlas) -- both without '#' -- is NOT always a
     matching pair: e.g. Ran.skel references atlas regions only present in
     Ran.atlas #436669, not Ran.atlas. So we try every combination.

     Each candidate is now a FILE OBJECT {name, dir, ext, size} because
     characters split across _Atlas / _SkeletonData sibling folders have
     their .skel in <Name>_SkeletonData/ and .atlas in <Name>_Atlas/.
     URLs are built from file.dir + file.name so the gallery fetches each
     file from its actual source folder.

     Order:
       1. Primary skel + primary atlas (most common case, fastest)
       2. Primary skel + alternate atlases (one-at-a-time)
       3. Alternate skels + each atlas
       4. JSON skeleton fallback (if any)
     De-duplicate while preserving order. */
  var skelList = (c.skel_candidates && c.skel_candidates.length ? c.skel_candidates.slice() : (c.skel ? [c.skel] : []));
  var atlasList = (c.atlas_candidates && c.atlas_candidates.length ? c.atlas_candidates.slice() : (c.atlas ? [c.atlas] : []));
  var combos = [];
  var seen = {};
  function addCombo(s, a) {
    if (!s || !a) return;
    var key = (s.dir || '') + '/' + s.name + '|' + (a.dir || '') + '/' + a.name;
    if (seen[key]) return;
    seen[key] = true;
    combos.push({skel: s, atlas: a});
  }
  /* Try all skel x atlas combinations (primary first). */
  for (var si = 0; si < skelList.length; si++) {
    for (var ai = 0; ai < atlasList.length; ai++) {
      addCombo(skelList[si], atlasList[ai]);
    }
  }
  /* Append JSON skeleton fallback as the last resort. */
  var jf = findJsonSkel(c);
  if (jf) {
    for (var aj = 0; aj < atlasList.length; aj++) {
      addCombo(jf, atlasList[aj]);
    }
  }

  var comboIdx = 0;

  function tryNextCombo(prevError) {
    if (comboIdx >= combos.length) {
      errEl.innerHTML = 'Failed to load: ' + (prevError || 'no valid skel+atlas combination found') +
        '<br><small style="color:#888">Tried ' + combos.length + ' combinations.</small>';
      errEl.style.display = 'block';
      return;
    }
    var combo = combos[comboIdx++];
    var skelUrl = fileUrl(combo.skel.dir || c.name, combo.skel.name);
    var atlasUrl = fileUrl(combo.atlas.dir || c.name, combo.atlas.name);
    var skelNameLow = combo.skel.name.toLowerCase();
    var isBinary = skelNameLow.endsWith('.skel') || skelNameLow.indexOf('.skel ') >= 0 || skelNameLow.endsWith('.skel.bin') || skelNameLow.endsWith('.skel.bytes') || skelNameLow.indexOf('.skel.bytes ') >= 0;

    var cfg = {};
    cfg[isBinary ? 'binaryUrl' : 'jsonUrl'] = skelUrl;
    cfg.atlasUrl = atlasUrl;
    cfg.showControls = true;
    cfg.backgroundColor = "#111118";
    cfg.alpha = true;
    cfg.preserveDrawingBuffer = true;
    cfg.success = function(p) {
      /* Quality check: if the skeleton renders with suspiciously small
         bounds (e.g. only a few attachments visible), this combo is
         probably broken -- try the next one. This catches characters
         like Elaine where the primary .skel+.atlas pair loads but
         only shows a tiny fragment because the default skin is missing
         most attachments or the atlas only has a subset of regions. */
      try {
        var skeleton = p.skeleton;
        if (skeleton) {
          autoSelectBestSkin(p);
          skeleton.setToSetupPose();
          if (p.animationState) {
            var tracks = p.animationState.tracks;
            for (var ti = 0; ti < tracks.length; ti++) {
              if (tracks[ti]) tracks[ti].trackTime = 0;
            }
            p.animationState.apply(skeleton);
          }
          skeleton.updateWorldTransform();
          var bOff = new spine.Vector2();
          var bSz = new spine.Vector2();
          var temps = [new spine.Vector2(), new spine.Vector2()];
          skeleton.getBounds(bOff, bSz, temps);
          var tooSmall = (bSz.x < 100 || bSz.y < 100);
          if (tooSmall && comboIdx < combos.length) {
            /* This combo is broken -- try the next one. */
            try { p.dispose(); } catch(e2) {}
            container.innerHTML = '<div style="text-align:center;padding:80px;color:#666"><div class="spinner"></div><br>Combo ' + comboIdx + ' rendered too small -- trying next...</div>';
            tryNextCombo('bounds too small: ' + Math.round(bSz.x) + 'x' + Math.round(bSz.y));
            return;
          }
        }
      } catch(qErr) { /* quality check failed -- accept this combo */ }
      autoSelectBestSkin(p);
      setupHighQualityRender(p);
      setupZoomPan(p);
    };
    cfg.error = function(p, reason) {
      /* This combination failed -- try the next one. */
      try { if (curPlayer) { curPlayer.dispose(); curPlayer = null; } } catch(e) {}
      container.innerHTML = '<div style="text-align:center;padding:80px;color:#666"><div class="spinner"></div><br>Trying combination ' + comboIdx + '/' + combos.length + '...</div>';
      tryNextCombo(reason);
    };
    container.innerHTML = '';
    try {
      curPlayer = new spine.SpinePlayer("player-container", cfg);
    } catch(e) {
      tryNextCombo(e.message);
    }
  }

  try {
    if (typeof spine === 'undefined' || !spine.SpinePlayer) throw new Error('SpinePlayer not loaded.');
    tryNextCombo(null);
  } catch(e) {
    container.innerHTML = '';
    errEl.innerHTML = 'Error: ' + e.message;
    errEl.style.display = 'block';
  }
}

// --- Export: Single PNG ---
function savePlayerImage() {
  if (!curPlayer || !curPlayer.sceneRenderer) { alert('No player active.'); return; }
  var renderer = curPlayer.sceneRenderer;
  var canvas = renderer.canvas;
  if (!canvas) return;
  try {
    // Re-render the current frame on a transparent background so the
    // exported PNG has alpha=0 where the skeleton isn't drawn.
    var gl = renderer.context.gl;
    var skeleton = curPlayer.skeleton;
    var savedTime = (curPlayer.animationState.tracks[0] || {}).trackTime;
    gl.clearColor(0, 0, 0, 0);
    gl.clear(gl.COLOR_BUFFER_BIT);
    renderer.begin();
    if (skeleton) renderer.drawSkeleton(skeleton, true);
    renderer.end();
    var a = document.createElement('a');
    a.download = (CHARS[curIdx] ? CHARS[curIdx].name : 'spine') + '.png';
    a.href = canvas.toDataURL('image/png');
    a.click();
  } catch(e) {
    alert('Could not save image: ' + e.message);
  }
}

// --- Export: Frame capture core ---
function captureAllFrames(fps, onProgress) {
  // Returns a Promise that resolves to {frames, fps, width, height}.
  // Yields to the event loop between frames so the UI can repaint and the
  // progress bar actually updates.
  return new Promise(function(resolve, reject) {
    if (!curPlayer || !curPlayer.skeleton || !curPlayer.sceneRenderer) {
      reject(new Error('Spine player not ready.')); return;
    }
    var skeleton = curPlayer.skeleton;
    var renderer = curPlayer.sceneRenderer;
    var gl = renderer.context.gl;
    var canvas = renderer.canvas;
    var w = canvas.width, h = canvas.height;
    var tracks = curPlayer.animationState.tracks;
    var track = null;
    for (var i = 0; i < tracks.length; i++) { if (tracks[i]) { track = tracks[i]; break; } }
    if (!track) { reject(new Error('No animation track is playing.')); return; }
    var anim = track.animation;
    var duration = anim.duration;
    if (duration <= 0) { reject(new Error('Animation has zero duration.')); return; }
    var totalFrames = Math.max(1, Math.ceil(duration * fps));
    var savedTime = track.trackTime;
    var savedPaused = curPlayer.paused;
    curPlayer.paused = true;
    var frames = [];
    var pixels = new Uint8Array(w * h * 4);
    var i = 0;

    function captureNext() {
      try {
        if (i >= totalFrames) {
          // Restore player state
          track.trackTime = savedTime;
          curPlayer.animationState.apply(skeleton);
          if (curPlayer.physics !== undefined) {
            skeleton.updateWorldTransform(curPlayer.physics);
          } else {
            skeleton.updateWorldTransform();
          }
          curPlayer.paused = savedPaused;
          resolve({ frames: frames, fps: fps, width: w, height: h });
          return;
        }
        var t = (i / fps) % duration;
        track.trackTime = t;
        curPlayer.animationState.apply(skeleton);
        // Spine 4.1: updateWorldTransform() takes no args.
        // Spine 4.2: requires a physics arg; pass curPlayer.physics if available.
        if (curPlayer.physics !== undefined) {
          skeleton.updateWorldTransform(curPlayer.physics);
        } else {
          skeleton.updateWorldTransform();
        }
        var cam = renderer.camera;
        if (cam) cam.update();
        // Clear to transparent black ourselves. The SpinePlayer main loop
        // would normally clear with its dark backgroundColor, but during
        // capture we want alpha=0 where the skeleton isn't drawn so PNG/GIF
        // exports come out with a transparent background.
        gl.clearColor(0, 0, 0, 0);
        gl.clear(gl.COLOR_BUFFER_BIT);
        renderer.begin();
        renderer.drawSkeleton(skeleton, true);
        renderer.end();
        gl.readPixels(0, 0, w, h, gl.RGBA, gl.UNSIGNED_BYTE, pixels);
        var imgData = new ImageData(w, h);
        for (var y = 0; y < h; y++) {
          var srcOff = (h - 1 - y) * w * 4;
          var dstOff = y * w * 4;
          for (var x = 0; x < w * 4; x++) imgData.data[dstOff + x] = pixels[srcOff + x];
        }
        frames.push(imgData);
        if (onProgress) onProgress(i + 1, totalFrames);
        i++;
        // Yield to the event loop so the progress bar can repaint
        setTimeout(captureNext, 0);
      } catch (e) {
        // Restore on error too, so the player isn't left in a paused state
        try {
          track.trackTime = savedTime;
          curPlayer.animationState.apply(skeleton);
          if (curPlayer.physics !== undefined) {
            skeleton.updateWorldTransform(curPlayer.physics);
          } else {
            skeleton.updateWorldTransform();
          }
          curPlayer.paused = savedPaused;
        } catch (_) {}
        reject(e);
      }
    }
    captureNext();
  });
}

function showExportProgress(current, total) {
  var el = $('export-overlay');
  el.style.display = 'flex';
  $('export-progress').value = current;
  $('export-progress').max = total;
  $('export-status').textContent = 'Capturing frame ' + current + ' / ' + total + '...';
}

function updateExportUI(status) {
  $('export-status').textContent = status;
}

function hideExportProgress() {
  setTimeout(function() { $('export-overlay').style.display = 'none'; }, 500);
}

// --- Export: PNG Sequence ZIP ---
async function exportPNGSequence() {
  if (typeof JSZip === 'undefined') { alert('JSZip not loaded. Check your internet connection (loaded from CDN).'); return; }
  if (exporting) return;
  exporting = true;
  var fps = 24;
  showExportProgress(0, 1);
  updateExportUI('Capturing frames...');
  await new Promise(function(r) { setTimeout(r, 50); });
  var result;
  try {
    result = await captureAllFrames(fps, function(cur, total) {
      showExportProgress(cur, total);
    });
  } catch (e) {
    updateExportUI('Error: ' + e.message);
    hideExportProgress();
    exporting = false;
    return;
  }
  updateExportUI('Creating ZIP...');
  var zip = new JSZip();
  var charName = CHARS[curIdx] ? CHARS[curIdx].name : 'spine';
  for (var i = 0; i < result.frames.length; i++) {
    var c2 = document.createElement('canvas');
    c2.width = result.width; c2.height = result.height;
    c2.getContext('2d').putImageData(result.frames[i], 0, 0);
    var blob = await new Promise(function(r) { c2.toBlob(r, 'image/png'); });
    var num = String(i).padStart(4, '0');
    zip.file(charName + '_frame_' + num + '.png', blob);
    showExportProgress(i + 1, result.frames.length);
    // Yield so UI updates
    if (i % 5 === 0) await new Promise(function(r) { setTimeout(r, 0); });
  }
  updateExportUI('Compressing...');
  var content = await zip.generateAsync({ type: 'blob' });
  var a = document.createElement('a');
  a.download = charName + '_frames.zip';
  a.href = URL.createObjectURL(content); a.click();
  URL.revokeObjectURL(a.href);
  updateExportUI('Done!');
  hideExportProgress();
  exporting = false;
}

// --- Export: Animated GIF ---
async function exportGIF() {
  if (typeof GIF === 'undefined') { alert('gif.js not loaded. Check your internet connection (loaded from CDN).'); return; }
  if (exporting) return;
  exporting = true;
  var fps = 15;
  showExportProgress(0, 1);
  updateExportUI('Capturing frames...');
  await new Promise(function(r) { setTimeout(r, 50); });
  var result;
  try {
    result = await captureAllFrames(fps, function(cur, total) {
      showExportProgress(cur, total);
    });
  } catch (e) {
    updateExportUI('Error: ' + e.message);
    hideExportProgress();
    exporting = false;
    return;
  }
  updateExportUI('Encoding GIF...');
  // gif.worker.js -- can't be loaded cross-origin on github.io (Worker CORS).
  // Fetch it as text, wrap in a Blob, and use the blob: URL (which is
  // same-origin) as the workerScript. Falls back to local lib/ if fetch fails.
  var workerUrl = null;
  try {
    var resp = await fetch('https://cdn.jsdelivr.net/npm/gif.js@0.2.0/dist/gif.worker.js');
    if (resp.ok) {
      var workerText = await resp.text();
      var workerBlob = new Blob([workerText], { type: 'application/javascript' });
      workerUrl = URL.createObjectURL(workerBlob);
    }
  } catch (e) { /* fall through to local */ }
  if (!workerUrl) workerUrl = 'lib/gif.worker.js';
  var gif = new GIF({
    workers: 2, quality: 10, width: result.width, height: result.height,
    workerScript: workerUrl
  });
  for (var i = 0; i < result.frames.length; i++) {
    var c2 = document.createElement('canvas');
    c2.width = result.width; c2.height = result.height;
    c2.getContext('2d').putImageData(result.frames[i], 0, 0);
    gif.addFrame(c2, { delay: Math.round(1000 / fps), copy: true });
    showExportProgress(i + 1, result.frames.length);
  }
  gif.on('progress', function(p) {
    updateExportUI('Encoding GIF... ' + Math.round(p * 100) + '%');
  });
  gif.on('finished', function(blob) {
    var a = document.createElement('a');
    a.download = (CHARS[curIdx] ? CHARS[curIdx].name : 'spine') + '.gif';
    a.href = URL.createObjectURL(blob); a.click();
    URL.revokeObjectURL(a.href);
    if (workerUrl && workerUrl.indexOf('blob:') === 0) URL.revokeObjectURL(workerUrl);
    updateExportUI('Done!');
    hideExportProgress();
    exporting = false;
  });
  gif.on('abort', function() {
    if (workerUrl && workerUrl.indexOf('blob:') === 0) URL.revokeObjectURL(workerUrl);
    hideExportProgress();
    exporting = false;
  });
  gif.render();
}

// --- Tabs & Modal ---
function switchTab(t) {
  document.querySelectorAll('.tab').forEach(function(x) { x.classList.remove('on'); });
  var tabEl = $('tab-' + t);
  if (tabEl) tabEl.classList.add('on');
  ['model', 'images', 'media', 'files'].forEach(function(id) {
    var el = $('pane-' + id);
    if (el) el.style.display = id === t ? 'block' : 'none';
  });
}

function buildMediaTab(c) {
  var el = $('pane-media');
  var html = '';
  c.files.forEach(function(f) {
    var url = fileUrl(f.dir || c.name, f.name);
    if (f.ext === '.mp4' || f.ext === '.webm') {
      html += '<div class="media-item"><p class="media-name">' + f.name + '</p><video controls src="' + url + '" style="max-width:100%;max-height:400px;border-radius:8px;"></video></div>';
    } else if (f.ext === '.wav' || f.ext === '.mp3' || f.ext === '.ogg' || f.ext === '.flac') {
      html += '<div class="media-item"><p class="media-name">' + f.name + '</p><audio controls src="' + url + '" style="width:100%;"></audio></div>';
    }
  });
  el.innerHTML = html || '<p style="color:#555;padding:40px;text-align:center">No audio or video files.</p>';
}

function buildImagesTab(c) {
  var el = $('pane-images');
  var imgs = c.files.filter(function(f) { return f.ext === '.png' || f.ext === '.jpg' || f.ext === '.jpeg' || f.ext === '.webp'; });
  if (!imgs.length) { el.innerHTML = '<p style="color:#555;padding:40px;text-align:center">No image files.</p>'; return; }
  el.innerHTML = '<div class="img-gallery">' + imgs.map(function(f) {
    var url = fileUrl(f.dir || c.name, f.name);
    return '<div class="img-item"><img src="' + url + '" loading="lazy"><p class="img-name">' + f.name + ' (' + fmtSize(f.size) + ')</p></div>';
  }).join('') + '</div>';
}

function openM(idx) {
  curIdx = idx;
  var c = CHARS[idx];
  /* Modal title: display_name (last path segment) + category subtitle.
     Full relative path goes in the title attribute for tooltip hover. */
  var dispName = c.display_name || c.name;
  var catLabel = c.category || '';
  $('mt').innerHTML = '<span style="color:#fff;">' + dispName + '</span>'
    + (catLabel ? '<span style="color:#6c5ce7;font-size:.75rem;font-weight:400;margin-left:10px;">' + catLabel + '</span>' : '');
  $('mt').title = c.name;
  buildImagesTab(c);
  buildMediaTab(c);
  $('flist').innerHTML = c.files.map(function(f) {
    /* All files live in c.thumb_dir (= c.name) under the Unity-native
       folder structure, so we don't need a dir label anymore. */
    return '<div class="mf"><span class="fn">' + f.name + '</span><span class="fs">' + fmtSize(f.size) + '</span></div>';
  }).join('');

  var acts = '';
  if (c.can_play) {
    acts += '<button class="btn btn-p" onclick="savePlayerImage()">Save PNG</button>';
    acts += '<button class="btn btn-p" onclick="exportPNGSequence()">PNG Sequence</button>';
    acts += '<button class="btn btn-p" onclick="exportGIF()">Save GIF</button>';
  }

  $('macts').innerHTML = acts;

  if (c.can_play) {
    switchTab('model');
    loadSpineModel(c);
  } else if (c.has_audio || c.has_video) {
    switchTab('media');
    $('player-container').innerHTML = '';
    $('player-error').style.display = 'none';
    $('pane-model').style.display = 'none';
  } else {
    switchTab('images');
    $('player-container').innerHTML = '';
    $('player-error').style.display = 'none';
    $('pane-model').style.display = 'none';
  }
  $('mo').classList.add('open');
  document.body.style.overflow = 'hidden';
}

function closeM() { destroyPlayer(); $('mo').classList.remove('open'); document.body.style.overflow = ''; curIdx = -1; }
function visList() { return filtered.map(function(c) { return CHARS.indexOf(c); }); }
function navP() { var v = visList(), i = v.indexOf(curIdx); if (i > 0) openM(v[i - 1]); }
function navN() { var v = visList(), i = v.indexOf(curIdx); if (i < v.length - 1) openM(v[i + 1]); }

document.addEventListener('keydown', function(e) {
  if (e.key === 'Escape') closeM();
  if ($('mo').classList.contains('open')) { if (e.key === 'ArrowLeft') navP(); if (e.key === 'ArrowRight') navN(); }
});
searchEl.addEventListener('input', function(e) { curSearch = e.target.value; applyF(); });

/* Populate the category dropdown from the unique set of categories in CHARS.
   Shows a count next to each category so users can see how many entries
   are in each. */
(function populateCategoryFilter() {
  var sel = $('cat-filter');
  if (!sel) return;
  var counts = {};
  CHARS.forEach(function(c) {
    var cat = c.category || '(uncategorized)';
    counts[cat] = (counts[cat] || 0) + 1;
  });
  var cats = Object.keys(counts).sort(function(a, b) {
    return a.toLowerCase().localeCompare(b.toLowerCase());
  });
  cats.forEach(function(cat) {
    var opt = document.createElement('option');
    opt.value = cat;
    opt.textContent = cat + ' (' + counts[cat] + ')';
    sel.appendChild(opt);
  });
  sel.addEventListener('change', function(e) {
    curCategory = e.target.value;
    applyF();
  });
})();

document.querySelectorAll('.fbtn').forEach(function(b) {
  b.addEventListener('click', function() {
    document.querySelectorAll('.fbtn').forEach(function(x) { x.classList.remove('on'); });
    b.classList.add('on'); curFilter = b.dataset.f; applyF();
  });
});
$('sort').addEventListener('change', function(e) { curSort = e.target.value; applyS(); });
applyF();'''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{root_name_safe} \u2014 Spine Gallery</title>
{spine_tags}
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
:root {{
  --bg:#0f0f13; --bg-card:#18181f; --bg-elev:#1a1a24; --bg-deep:#12121a;
  --border:#25252f; --border-strong:#2f2f3f;
  --text:#e8e8ec; --text-mid:#aaa; --text-dim:#666; --text-faint:#444;
  --accent:#6c5ce7; --accent-hi:#a29bfe; --accent-lo:#5a4bd1;
  --play:#00b894; --warn:#e17055; --gold:#fdcb6e;
  --radius-sm:6px; --radius-md:10px; --radius-lg:14px;
  --shadow-card:0 4px 14px rgba(0,0,0,.25);
  --shadow-hover:0 12px 30px rgba(108,92,231,.18);
  --gap-grid:14px;
}}
html {{ scroll-behavior:smooth; }}
body {{ font-family:'Inter','Segoe UI',system-ui,sans-serif; background:var(--bg); color:var(--text); min-height:100vh; -webkit-font-smoothing:antialiased; }}

/* --- Header --- */
header {{ position:sticky; top:0; z-index:100; background:rgba(15,15,19,.92); backdrop-filter:blur(16px) saturate(140%); border-bottom:1px solid var(--border); padding:14px 24px 10px; }}
.htop {{ display:flex; align-items:center; justify-content:space-between; gap:14px; margin-bottom:10px; }}
h1 {{ font-size:1.25rem; font-weight:700; color:#fff; display:flex; align-items:center; gap:10px; letter-spacing:-.01em; }}
h1 .ct {{ font-size:.75rem; font-weight:500; color:var(--text-dim); background:#1e1e28; padding:3px 10px; border-radius:20px; border:1px solid var(--border); }}
.hdr-right {{ display:flex; align-items:center; gap:8px; }}
.hdr-right .btn, .hdr-right button {{ padding:6px 12px; font-size:.78rem; }}

/* --- Controls bar --- */
.ctrls {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
#search {{ flex:1; min-width:200px; max-width:420px; padding:8px 14px 8px 36px; border-radius:var(--radius-md); border:1px solid var(--border); background:var(--bg-elev) url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='14' height='14' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Ccircle cx='11' cy='11' r='8'/%3E%3Cpath d='m21 21-4.3-4.3'/%3E%3C/svg%3E") no-repeat 12px center; color:var(--text); font-size:.88rem; outline:none; transition:border-color .15s, box-shadow .15s; }}
#search:focus {{ border-color:var(--accent); box-shadow:0 0 0 3px rgba(108,92,231,.15); }}
#search::placeholder {{ color:#555; }}
#cat-filter, #sort {{ padding:7px 28px 7px 12px; border-radius:var(--radius-md); border:1px solid var(--border); background:var(--bg-elev) var(--chevron,#1a1a24); color:var(--text-mid); font-size:.82rem; outline:none; cursor:pointer; appearance:none; -webkit-appearance:none; background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='12' height='12' viewBox='0 0 24 24' fill='none' stroke='%23666' stroke-width='2' stroke-linecap='round' stroke-linejoin='round'%3E%3Cpolyline points='6 9 12 15 18 9'/%3E%3C/svg%3E"); background-repeat:no-repeat; background-position:right 10px center; transition:border-color .15s; }}
#cat-filter:hover, #sort:hover {{ border-color:var(--accent); }}

.fbtn {{ padding:7px 13px; border-radius:var(--radius-md); border:1px solid var(--border); background:var(--bg-elev); color:var(--text-mid); font-size:.78rem; font-weight:500; cursor:pointer; transition:all .15s; white-space:nowrap; }}
.fbtn:hover {{ border-color:var(--accent); color:var(--text); background:#20202a; }}
.fbtn.on {{ background:var(--accent); border-color:var(--accent); color:#fff; box-shadow:0 2px 8px rgba(108,92,231,.3); }}

#stats {{ font-size:.78rem; color:var(--text-dim); margin-top:8px; padding-top:8px; border-top:1px solid var(--border); }}


/* --- Grid --- */
#grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:var(--gap-grid); padding:18px 24px 24px; }}

/* --- Card --- */
.card {{ background:var(--bg-card); border:1px solid var(--border); border-radius:var(--radius-md); overflow:hidden; cursor:pointer; transition:transform .15s ease, border-color .15s, box-shadow .15s; position:relative; display:flex; flex-direction:column; }}
.card:hover {{ transform:translateY(-3px); border-color:var(--accent); box-shadow:var(--shadow-hover); }}


.cthumb {{ width:100%; aspect-ratio:1; background:linear-gradient(135deg,#0e0e15 0%,#16161e 100%); display:flex; align-items:center; justify-content:center; overflow:hidden; position:relative; border-bottom:1px solid var(--border); }}
.cthumb img {{ max-width:90%; max-height:90%; object-fit:contain; image-rendering:pixelated; opacity:.95; transition:opacity .15s, transform .15s; }}
.card:hover .cthumb img {{ opacity:1; transform:scale(1.03); }}
.cthumb .noimg {{ color:var(--text-faint); font-size:2.2rem; font-weight:300; }}

/* Badges: top-right, wrap nicely */
.bdg {{ position:absolute; top:6px; right:6px; display:flex; gap:3px; flex-wrap:wrap; justify-content:flex-end; max-width:calc(100% - 12px); z-index:6; pointer-events:none; }}
.b {{ font-size:.55rem; padding:2px 6px; border-radius:4px; font-weight:700; text-transform:uppercase; letter-spacing:.03em; backdrop-filter:blur(4px); }}
.b-s {{ background:rgba(0,184,148,.9); color:#fff; }}
.b-a {{ background:rgba(9,132,227,.9); color:#fff; }}
.b-j {{ background:rgba(253,203,110,.9); color:#222; }}
.b-o {{ background:rgba(225,112,85,.9); color:#fff; }}
.b-w {{ background:rgba(108,92,231,.9); color:#fff; }}
.b-v {{ background:rgba(232,67,147,.9); color:#fff; }}

.cplay {{ position:absolute; bottom:8px; left:8px; width:28px; height:28px; border-radius:50%; background:rgba(108,92,231,.92); display:flex; align-items:center; justify-content:center; font-size:.7rem; color:#fff; pointer-events:none; z-index:6; box-shadow:0 2px 8px rgba(0,0,0,.4); }}



.cinfo {{ padding:8px 10px 9px; flex:1; display:flex; flex-direction:column; gap:1px; min-height:62px; }}
.ccat {{ font-size:.62rem; color:var(--accent-hi); font-weight:700; text-transform:uppercase; letter-spacing:.06em; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; opacity:.85; }}
.cname {{ font-size:.82rem; font-weight:600; color:var(--text); white-space:nowrap; overflow:hidden; text-overflow:ellipsis; line-height:1.25; }}
.cmeta {{ font-size:.7rem; color:var(--text-dim); margin-top:auto; padding-top:2px; }}

/* --- Modal --- */
#mo {{ display:none; position:fixed; inset:0; z-index:200; background:rgba(0,0,0,.88); backdrop-filter:blur(6px); align-items:center; justify-content:center; padding:16px; }}
#mo.open {{ display:flex; }}
#md {{ background:var(--bg-elev); border:1px solid var(--border-strong); border-radius:var(--radius-lg); width:100%; max-width:1000px; max-height:92vh; overflow:hidden; display:flex; flex-direction:column; box-shadow:0 24px 64px rgba(0,0,0,.6); }}
.mh {{ display:flex; justify-content:space-between; align-items:center; padding:14px 18px; border-bottom:1px solid var(--border); flex-shrink:0; gap:10px; }}
.mh h2 {{ font-size:1.1rem; color:#fff; font-weight:600; word-break:break-all; }}
.mx {{ width:32px; height:32px; border-radius:var(--radius-sm); border:none; background:var(--border); color:var(--text-mid); font-size:1.3rem; cursor:pointer; display:flex; align-items:center; justify-content:center; transition:all .15s; }}
.mx:hover {{ background:var(--accent); color:#fff; }}
.mb {{ padding:0 18px 18px; overflow:auto; flex:1; }}

/* --- Player area --- */
#player-wrap {{ width:100%; background:#111118; border-radius:var(--radius-md); overflow:hidden; position:relative; margin-bottom:12px; border:1px solid var(--border); }}
#player-container {{ width:100%; height:640px; position:relative; }}
#player-error {{ display:none; text-align:center; padding:30px; color:var(--warn); font-size:.9rem; line-height:1.6; }}
.zoom-hint {{ position:absolute; top:8px; right:8px; font-size:.68rem; color:rgba(255,255,255,.55); pointer-events:none; z-index:10; background:rgba(0,0,0,.4); padding:3px 8px; border-radius:4px; backdrop-filter:blur(4px); }}

/* --- Skin selector --- */
#skin-select-wrap {{ display:none; align-items:center; gap:8px; margin-bottom:8px; padding:8px 12px; background:var(--bg-deep); border-radius:var(--radius-sm); }}
#skin-select-wrap label {{ font-size:.78rem; color:var(--text-mid); white-space:nowrap; font-weight:500; }}
#skin-select {{ padding:5px 10px; border-radius:var(--radius-sm); border:1px solid var(--border); background:var(--bg-card); color:var(--text); font-size:.8rem; outline:none; max-width:300px; }}

/* --- Tabs --- */
.tabs {{ display:flex; gap:4px; margin-bottom:12px; flex-wrap:wrap; border-bottom:1px solid var(--border); padding-bottom:0; }}
.tab {{ padding:8px 16px; border-radius:var(--radius-sm) var(--radius-sm) 0 0; border:1px solid transparent; border-bottom:none; background:transparent; color:var(--text-mid); font-size:.82rem; cursor:pointer; font-weight:500; transition:all .15s; }}
.tab:hover {{ color:var(--text); background:rgba(255,255,255,.03); }}
.tab.on {{ background:var(--bg-deep); color:#fff; border-color:var(--border); border-bottom-color:var(--bg-deep); position:relative; }}
.tab.on::after {{ content:''; position:absolute; left:0; right:0; bottom:-1px; height:2px; background:var(--accent); }}

/* --- Image / media panes --- */
#pane-images {{ text-align:center; background:var(--bg-deep); border-radius:var(--radius-md); padding:16px; max-height:60vh; overflow:auto; }}
.img-gallery {{ display:flex; flex-direction:column; gap:14px; align-items:center; }}
.img-item img {{ max-width:100%; max-height:50vh; object-fit:contain; border-radius:var(--radius-sm); }}
.img-name {{ font-size:.72rem; color:var(--text-dim); margin-top:4px; word-break:break-all; }}
#pane-media {{ padding:8px 0; }}
.media-item {{ margin-bottom:16px; }}
.media-name {{ font-size:.78rem; color:var(--text-mid); margin-bottom:6px; word-break:break-all; }}
.mfiles {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:5px; }}
.mf {{ padding:6px 10px; background:var(--bg-deep); border-radius:var(--radius-sm); font-size:.75rem; color:var(--text-mid); display:flex; justify-content:space-between; align-items:center; overflow:hidden; border:1px solid var(--border); }}
.mf .fn {{ white-space:nowrap; overflow:hidden; text-overflow:ellipsis; margin-right:6px; }}
.mf .fs {{ color:var(--text-dim); flex-shrink:0; }}
.mf .fd {{ color:var(--accent-hi); font-size:.7rem; margin-right:6px; flex-shrink:0; opacity:.8; }}

/* --- Action buttons row --- */
.macts {{ display:flex; gap:8px; flex-wrap:wrap; margin-top:14px; padding-top:12px; border-top:1px solid var(--border); align-items:center; }}
.btn {{ padding:8px 16px; border-radius:var(--radius-md); border:none; font-size:.82rem; font-weight:600; cursor:pointer; transition:all .15s; text-decoration:none; display:inline-flex; align-items:center; gap:6px; font-family:inherit; }}
.btn:active {{ transform:scale(.97); }}
.btn-p {{ background:var(--accent); color:#fff; }}
.btn-p:hover {{ background:var(--accent-lo); box-shadow:0 4px 12px rgba(108,92,231,.3); }}
.btn-s {{ background:var(--bg-deep); color:var(--text-mid); border:1px solid var(--border); }}
.btn-s:hover {{ background:var(--border-strong); color:var(--text); }}
.btn-g {{ background:var(--play); color:#fff; }}
.btn-g:hover {{ background:#00a381; }}
.btn-d {{ background:var(--warn); color:#fff; }}
.btn-d:hover {{ background:#d35f47; }}
.nav-btn {{ padding:6px 12px; font-size:.78rem; }}

/* --- Empty / loading / footer --- */
#empty {{ display:none; text-align:center; padding:80px 20px; color:var(--text-dim); }}
#empty .icon {{ font-size:3rem; margin-bottom:10px; opacity:.5; }}
#site-footer {{ text-align:center; padding:28px 16px 36px; margin-top:24px; color:var(--text-dim); font-size:.76rem; line-height:1.6; border-top:1px solid var(--border); background:rgba(0,0,0,.2); }}
#site-footer p {{ margin:0; max-width:680px; margin-inline:auto; }}
#loading {{ text-align:center; padding:80px 20px; color:var(--text-dim); }}
.spinner {{ width:36px; height:36px; margin:0 auto 14px; border:3px solid var(--border); border-top-color:var(--accent); border-radius:50%; animation:spin .8s linear infinite; }}
@keyframes spin {{ to {{ transform:rotate(360deg); }} }}

/* --- Spine player overrides --- */
#player-container .spine-player {{ background:transparent!important; }}
#player-container .spine-player canvas {{ display:block; width:100%!important; height:100%!important; }}

/* --- Export overlay --- */
#export-overlay {{ display:none; position:fixed; inset:0; z-index:500; background:rgba(0,0,0,.85); backdrop-filter:blur(4px); align-items:center; justify-content:center; flex-direction:column; gap:16px; }}
#export-overlay.open {{ display:flex; }}
#export-box {{ background:var(--bg-elev); border:1px solid var(--border-strong); border-radius:var(--radius-lg); padding:28px 36px; text-align:center; min-width:340px; box-shadow:0 24px 64px rgba(0,0,0,.6); }}
#export-progress {{ width:100%; height:8px; border-radius:4px; background:var(--border); margin:14px 0 10px; -webkit-appearance:none; appearance:none; overflow:hidden; }}
#export-progress::-webkit-progress-bar {{ background:var(--border); border-radius:4px; }}
#export-progress::-webkit-progress-value {{ background:linear-gradient(90deg,var(--accent),var(--accent-hi)); border-radius:4px; transition:width .15s; }}
#export-progress::-moz-progress-bar {{ background:var(--accent); border-radius:4px; }}
#export-status {{ font-size:.88rem; color:var(--text-mid); }}

/* --- Mobile zoom controls --- */
#mobile-zoom-controls {{ position:absolute; bottom:60px; left:12px; display:flex; flex-direction:column; gap:6px; z-index:10; }}
#mobile-zoom-controls button {{ width:36px; height:36px; border-radius:50%; border:1px solid var(--border-strong); background:rgba(26,26,36,.85); color:var(--text); font-size:1.1rem; font-weight:700; cursor:pointer; display:flex; align-items:center; justify-content:center; backdrop-filter:blur(8px); transition:all .15s; line-height:1; padding:0; }}
#mobile-zoom-controls button:hover {{ background:var(--accent); color:#fff; border-color:var(--accent); }}
#mobile-zoom-controls button:active {{ transform:scale(.9); }}

/* --- Scrollbar polish (WebKit) --- */
::-webkit-scrollbar {{ width:10px; height:10px; }}
::-webkit-scrollbar-track {{ background:transparent; }}
::-webkit-scrollbar-thumb {{ background:var(--border-strong); border-radius:5px; border:2px solid var(--bg); }}
::-webkit-scrollbar-thumb:hover {{ background:var(--accent); }}

/* --- Responsive --- */
@media (max-width:640px) {{
  #grid {{ grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); gap:10px; padding:12px; }}
  header {{ padding:12px 14px 8px; }}
  h1 {{ font-size:1.05rem; }}
  .ctrls {{ gap:6px; }}
  .fbtn {{ padding:6px 10px; font-size:.72rem; }}
  #player-container {{ height:420px; }}
  #md {{ max-width:100%; }}
}}
</style>
</head>
<body>
<header>
  <div class="htop">
    <h1>{root_name_safe} <span class="ct" id="tc"></span></h1>

  </div>
  <div class="ctrls">
    <input type="text" id="search" placeholder="Search characters..." autocomplete="off">
    <select id="cat-filter" title="Filter by category">
      <option value="">All Categories</option>
    </select>
    <button class="fbtn on" data-f="all">Characters</button>
    <button class="fbtn" data-f="playable">Playable</button>
    <button class="fbtn" data-f="skel">Has .skel</button>
    <button class="fbtn" data-f="media">Media</button>
    <button class="fbtn" data-f="noimg">No Image</button>
    <button class="fbtn" data-f="allfolders">All Folders</button>
    <select id="sort">
      <option value="name-asc">Name A-Z</option>
      <option value="name-desc">Name Z-A</option>
      <option value="files-desc">Most Files</option>
      <option value="size-desc">Largest</option>
      <option value="size-asc">Smallest</option>
    </select>

  </div>
  <div id="stats"></div>
</header>

<div id="loading"><div class="spinner"></div>Loading {len(characters)} characters...</div>
<div id="grid"></div>
<div id="empty"><div class="icon">&#128269;</div><p>No characters match your search.</p></div>

<footer id="site-footer">
  <p>This is a fan project and not associated with Wemade Connect. Copyright for all Assets: &copy;2024. Wemade Connect Corp. CODECAT, Inc. All rights reserved.</p>
</footer>



<div id="mo" onclick="if(event.target===this)closeM()">
  <div id="md">
    <div class="mh">
      <h2 id="mt"></h2>
      <div style="display:flex;gap:6px;align-items:center;">
        <button class="btn btn-s nav-btn" onclick="navP()" title="Previous (Left arrow)">&#8592;</button>
        <button class="btn btn-s nav-btn" onclick="navN()" title="Next (Right arrow)">&#8594;</button>
        <button class="mx" onclick="closeM()" title="Close (Esc)">&times;</button>
      </div>
    </div>
    <div class="mb">
      <div class="tabs">
        <button class="tab" id="tab-model" onclick="switchTab('model')">Model</button>
        <button class="tab" id="tab-images" onclick="switchTab('images')">Images</button>
        <button class="tab" id="tab-media" onclick="switchTab('media')">Media</button>
        <button class="tab" id="tab-files" onclick="switchTab('files')">Files</button>
      </div>
      <div id="pane-model">
        <div id="player-wrap">
          <div id="player-container"></div>
          <div class="zoom-hint">Scroll/pinch to zoom &middot; Drag to pan</div>
          <div id="mobile-zoom-controls">
            <button onclick="mobileZoomIn()" title="Zoom In">+</button>
            <button onclick="mobileZoomOut()" title="Zoom Out">&minus;</button>
            <button onclick="mobileZoomReset()" title="Reset View">&circlearrowleft;</button>
          </div>
          <div id="player-error"></div>
        </div>
        <div id="skin-select-wrap">
          <label for="skin-select">Skin:</label>
          <select id="skin-select" onchange="onSkinChange()"></select>
        </div>

      </div>
      <div id="pane-images" style="display:none;"></div>
      <div id="pane-media" style="display:none;"></div>
      <div id="pane-files" style="display:none;"><div class="mfiles" id="flist"></div></div>
      <div class="macts" id="macts"></div>
    </div>
  </div>
</div>

<div id="export-overlay">
  <div id="export-box">
    <div class="spinner"></div>
    <p id="export-status">Preparing...</p>
    <progress id="export-progress" value="0" max="1"></progress>
  </div>
</div>

<script>
{js_code}
</script>
</body>
</html>"""
    return html


def build_generate_thumbs_html(characters, has_spine_local):
    playable = [c for c in characters if c['can_play'] and not c['has_thumb']]
    chars_json = json.dumps(playable, ensure_ascii=False)

    if not has_spine_local:
        return None

    js_code = 'var QUEUE = ' + chars_json + ';' + r'''
var ROOT = "./";
/* Encode a forward-slash-separated relative path for use in a URL.
   encodeURIComponent alone turns 'a/b/c' into 'a%2Fb%2Fc' which the
   static HTTP server treats as a single path segment (404). */
function encodePath(p) {
  if (!p) return '';
  return String(p).split('/').map(encodeURIComponent).join('/');
}
function fileUrl(dir, name) {
  return ROOT + encodePath(dir) + '/' + encodeURIComponent(name);
}
/* Use the page's own origin for the save_thumb endpoint.
   Hardcoding "http://localhost:8081" breaks when the user accesses via
   "127.0.0.1" or a LAN IP -- the browser treats them as different origins
   and blocks the fetch with "TypeError: Failed to fetch". */
var SERVER = window.location.origin;
var running = false;
var idx = 0;
var done = 0, failed = 0;
var startTime = 0;

var logEl = document.getElementById('log');
var statusEl = document.getElementById('status');
var barEl = document.getElementById('progress-bar-inner');
var nameEl = document.getElementById('current-name');
var totalEl = document.getElementById('total-count');
var doneEl = document.getElementById('done-count');
var failEl = document.getElementById('fail-count');
var etaEl = document.getElementById('eta');
var startBtn = document.getElementById('start-btn');
var stopBtn = document.getElementById('stop-btn');

totalEl.textContent = QUEUE.length;

function log(msg, cls) {
  var d = document.createElement('div');
  if (cls) d.className = cls;
  d.textContent = msg;
  logEl.appendChild(d);
  logEl.scrollTop = logEl.scrollHeight;
}

function updateUI() {
  var pct = QUEUE.length ? (done / QUEUE.length * 100) : 0;
  barEl.style.width = pct.toFixed(1) + '%';
  statusEl.textContent = done + ' / ' + QUEUE.length + ' (' + pct.toFixed(1) + '%)';
  doneEl.textContent = done;
  failEl.textContent = failed;
  if (done > 0 && running) {
    var elapsed = (Date.now() - startTime) / 1000;
    var perItem = elapsed / done;
    var remaining = (QUEUE.length - idx) * perItem;
    var m = Math.floor(remaining / 60);
    var s = Math.floor(remaining % 60);
    etaEl.textContent = m + 'm ' + s + 's';
  } else if (!running) {
    etaEl.textContent = '--';
  }
}

function findJsonSkel(c) {
  var js = c.files.find(function(f){return f.ext==='.json'&&f.name.indexOf('SkeletonData')!==-1&&f.name.indexOf('#')===-1;});
  return js || c.files.find(function(f){return f.ext==='.json'&&f.name.indexOf('SkeletonData')!==-1;});
}

function autoSelectBestSkin(player) {
  try {
    var skeleton = player.skeleton;
    if (!skeleton || !skeleton.data || !skeleton.data.skins) return;
    var skins = skeleton.data.skins;
    if (skins.length <= 1) return;
    /* Same logic as gallery version -- uses attachmentName (correct) not
       slot.name (was wrong), and prefers skins named default/base/normal. */
    var bestSkin = null, bestCount = -1;
    var defaultSkin = null;
    for (var i = 0; i < skins.length; i++) {
      var skin = skins[i];
      var nm = (skin.name || '').toLowerCase();
      if (nm === 'default' || nm === 'base' || nm === 'normal') defaultSkin = skin.name;
      var count = 0;
      for (var j = 0; j < skeleton.data.slots.length; j++) {
        var slot = skeleton.data.slots[j];
        if (slot.attachmentName && skin.getAttachment(slot.index, slot.attachmentName)) count++;
      }
      if (count > bestCount) { bestCount = count; bestSkin = skin.name; }
    }
    if (defaultSkin && bestCount > 0) {
      var defSkin = skins.find(function(s){return s.name === defaultSkin;});
      if (defSkin) {
        var defaultCount = 0;
        for (var k = 0; k < skeleton.data.slots.length; k++) {
          var sl = skeleton.data.slots[k];
          if (sl.attachmentName && defSkin.getAttachment(sl.index, sl.attachmentName)) defaultCount++;
        }
        if (defaultCount >= bestCount * 0.5) bestSkin = defaultSkin;
      }
    }
    if (bestSkin && bestSkin !== skeleton.skin.name) {
      skeleton.setSkinByName(bestSkin);
      skeleton.setSlotsToSetupPose();
    }
  } catch(e) {}
}

function processNext() {
  if (!running || idx >= QUEUE.length) {
    running = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    document.getElementById('player-area').innerHTML = '<div style="display:flex;align-items:center;justify-content:center;height:100%;color:#00b894;font-size:1rem;">Done! Generated ' + done + ' thumbnails.</div>';
    statusEl.textContent = 'Complete! ' + done + ' generated, ' + failed + ' failed.';
    log('Finished. ' + done + ' thumbnails saved, ' + failed + ' failed.', 'ok');
    return;
  }

  var c = QUEUE[idx];
  nameEl.textContent = c.name;
  var container = document.getElementById('player-area');
  container.innerHTML = '';
  /* Make the container visible in-viewport so WebGL actually renders.
     z-index:-1 puts it behind other content; the player needs real viewport
     space to render, otherwise browsers skip the draw calls. */
  container.style.cssText = 'width:320px;height:320px;position:fixed;top:0;left:0;z-index:-1;pointer-events:none;overflow:hidden;';

  /* Build the list of (skel, atlas) combinations to try.
     Same logic as loadSpineModel in the gallery -- characters extracted
     from Unity bundles may have multiple .skel / .atlas files (with
     '#NNNNN' suffixes from AssetStudio), and the primary pair is not
     always a matching pair. Try all combinations until one loads.

     Each candidate is a FILE OBJECT {name, dir, ext, size} because
     characters split across _Atlas / _SkeletonData sibling folders have
     their .skel in <Name>_SkeletonData/ and .atlas in <Name>_Atlas/. */
  var skelList = (c.skel_candidates && c.skel_candidates.length ? c.skel_candidates.slice() : (c.skel ? [c.skel] : []));
  var atlasList = (c.atlas_candidates && c.atlas_candidates.length ? c.atlas_candidates.slice() : (c.atlas ? [c.atlas] : []));
  var combos = [];
  var seenCombo = {};
  function addCombo(s, a) {
    if (!s || !a) return;
    var key = (s.dir || '') + '/' + s.name + '|' + (a.dir || '') + '/' + a.name;
    if (seenCombo[key]) return;
    seenCombo[key] = true;
    combos.push({skel: s, atlas: a});
  }
  for (var si = 0; si < skelList.length; si++) {
    for (var ai = 0; ai < atlasList.length; ai++) {
      addCombo(skelList[si], atlasList[ai]);
    }
  }
  var jf = findJsonSkel(c);
  if (jf) {
    for (var aj = 0; aj < atlasList.length; aj++) {
      addCombo(jf, atlasList[aj]);
    }
  }

  if (combos.length === 0) {
    log('No skeleton: ' + c.name, 'err');
    failed++; idx++; updateUI();
    container.style.cssText = '';
    setTimeout(processNext, 30);
    return;
  }

  /* Track the BEST combo's render so we can pick the one with the largest
     bounds area. Some characters have multiple .skel/.atlas versions where
     one combo loads OK but renders only a tiny fragment of the character
     (e.g. Elaine's combo 1 has bounds 187x398 = 74k area, but combo 4 has
     bounds 1640x2038 = 3.3M area -- 45x more content). We can't tell from
     file size or name which is "correct", so we render all combos that load
     successfully and pick the one with the most visible content. */
  var bestRender = null;  /* { dataUrl, area, comboDesc } */

  var comboIdx = 0;
  function saveBestRender() {
    if (!bestRender) {
      log('No render produced: ' + c.name, 'err');
      failed++; idx++; updateUI();
      container.style.cssText = '';
      setTimeout(processNext, 30);
      return;
    }
    var b64 = bestRender.dataUrl.split(',')[1];
    /* thumb.png lives in c.thumb_dir, which is c.name for normal characters
       but for characters split across sibling _Atlas/_SkeletonData folders
       with no main folder on disk, it's the first sibling folder. */
    var thumbDir = c.thumb_dir || c.name;
    fetch(SERVER + '/save_thumb', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: c.name, dir: thumbDir, data: b64})
    }).then(function(r) { return r.json(); }).then(function() {
      log('OK: ' + c.name + ' (' + bestRender.bounds + ', area=' + Math.round(bestRender.area) + ', ' + bestRender.comboDesc + ')', 'ok');
      done++; idx++; updateUI();
      container.style.cssText = '';
      setTimeout(processNext, 30);
    }).catch(function(e) {
      log('Save error: ' + c.name + ' - ' + e, 'err');
      failed++; idx++; updateUI();
      container.style.cssText = '';
      setTimeout(processNext, 30);
    });
  }

  function tryNextCombo(prevError) {
    if (comboIdx >= combos.length) {
      /* No more combos to try. If we have a best render, save it. */
      if (bestRender) {
        saveBestRender();
      } else {
        log('All combos failed: ' + c.name + ' - ' + (prevError || 'no combo worked'), 'err');
        failed++; idx++; updateUI();
        container.style.cssText = '';
        setTimeout(processNext, 30);
      }
      return;
    }
    var combo = combos[comboIdx++];
    var skelUrl = fileUrl(combo.skel.dir || c.name, combo.skel.name);
    var atlasUrl = fileUrl(combo.atlas.dir || c.name, combo.atlas.name);
    var skelLow = combo.skel.name.toLowerCase();
    var isBinary = skelLow.endsWith('.skel') || skelLow.indexOf('.skel ') >= 0 || skelLow.endsWith('.skel.bin') || skelLow.endsWith('.skel.bytes') || skelLow.indexOf('.skel.bytes ') >= 0;

    var cfg = {
      showControls: false,
      backgroundColor: '#12121a',
      alpha: true,
      atlasUrl: atlasUrl,
      preserveDrawingBuffer: true,
      success: function(p) {
      /* === THUMBNAIL GENERATION ===
         Goal: capture the "assembled character" -- the artist-defined default
         pose, every bone in its proper place, every slot showing its default
         skin attachment. NOT a random frame of animation.

         The previous implementation called `p.animationState.apply(skeleton)`
         which captured whatever frame of the default animation happened to be
         playing at ~300ms -- often mid-jump, mid-attack, or with limbs in
         impossible positions. That is why thumbnails looked broken.

         The fix below:
           1. Pauses the player's render loop (so it can't overwrite our pose).
           2. Picks the skin with the most attachments (most "complete" look).
           3. Resets the skeleton to its SETUP POSE (the assembled default).
              setToSetupPose() also re-populates slot attachments from the
              current skin, so all body parts show up.
           3b. Applies the FIRST FRAME of the default animation. This handles
              characters whose setup pose is intentionally scattered (artists
              sometimes build characters with pieces spread out for editing,
              then assemble them via the first frame of an idle animation).
              For normal characters, the first frame of idle ~= setup pose.
           4. Updates world transforms so bone positions are correct.
           5. Computes bounds on the assembled pose (not on a random anim frame).
           6. Frames the camera to fit those bounds with 20% padding.
           7. Renders one frame, then captures via toDataURL with pixel validation.
      */
      function tryRender(attempt) {
        /* Wait for textures to upload to GPU. The player fires `success` as
           soon as the skeleton is parsed; texture uploads finish afterwards.
           600ms covers most characters; complex ones get a few retries. */
        var wait = attempt === 0 ? 600 : 400;
        setTimeout(function() {
          try {
            var renderer = p.sceneRenderer;
            if (!renderer || !renderer.canvas) throw new Error('no renderer');
            var skeleton = p.skeleton;
            if (!skeleton) throw new Error('no skeleton');
            var canvas = renderer.canvas;

            /* 1. Pause the player so its render loop won't overwrite our draw. */
            try { p.paused = true; } catch(pe) {}

            /* 2. Pick the skin with the most attachments (most "complete" look).
                  Done AFTER pausing so animation timelines can't override it. */
            autoSelectBestSkin(p);

            /* 3. RESET TO SETUP POSE -- the "assembled character" look.
                  This resets every bone to its artist-defined default position
                  AND repopulates slot attachments from the (just-set) skin, so
                  no body parts are missing. */
            skeleton.setToSetupPose();

            /* 3b. APPLY FIRST FRAME OF THE DEFAULT ANIMATION.
                  Some Spine artists build characters with the pieces scattered
                  in the setup pose (easier to select individual parts in the
                  editor), then assemble them via the first frame of an idle
                  animation. For such characters, setToSetupPose() alone shows
                  a "shattered" character with pieces floating in space.
                  Applying the first frame of the default animation assembles
                  them. For normal characters (where setup pose is already a
                  clean T/A-pose), the first frame of idle is essentially the
                  same as setup pose, so this is safe.
                  We set trackTime=0 on all tracks BEFORE applying, so we
                  capture the first frame (not a mid-animation frame). */
            try {
              if (p.animationState) {
                var tracks = p.animationState.tracks;
                for (var ti = 0; ti < tracks.length; ti++) {
                  if (tracks[ti]) {
                    tracks[ti].trackTime = 0;
                  }
                }
                p.animationState.apply(skeleton);
              }
            } catch(animErr) { /* ignore -- fall back to setup pose */ }

            /* 4. Compute world-space bone transforms so positions are correct. */
            skeleton.updateWorldTransform();

            /* 5. Compute bounds on the assembled character. */
            var bOff = new spine.Vector2();
            var bSz = new spine.Vector2();
            var temps = [new spine.Vector2(), new spine.Vector2()];
            skeleton.getBounds(bOff, bSz, temps);

            /* 6. Frame camera on the bounds. Use canvas.width (the WebGL
                  buffer dim) rather than clientWidth, which can be 0 before
                  layout completes. */
            var cw = canvas.width || 320;
            var ch = canvas.height || 320;

            if (bSz.x > 5 && bSz.y > 5) {
              /* Spine Camera: visible_world = viewport * zoom.
                 Higher zoom = MORE world visible = character SMALLER on screen.
                 We want the character to fit with ~20% padding. */
              renderer.camera.position.x = bOff.x + bSz.x / 2;
              renderer.camera.position.y = bOff.y + bSz.y / 2;
              renderer.camera.zoom = Math.max(bSz.x / cw, bSz.y / ch) * 1.2;
            } else {
              /* Empty / degenerate bounds. Default view at origin. */
              renderer.camera.position.x = 0;
              renderer.camera.position.y = 0;
              renderer.camera.zoom = 1;
            }
            renderer.camera.update();

            /* 7. Manually render one frame. Let renderer.begin() handle the
                  clear (it calls gl.clear internally); our own gl.clear here
                  is just insurance in case the renderer's clear is disabled. */
            var gl = renderer.context && renderer.context.gl;
            renderer.begin();
            renderer.drawSkeleton(skeleton, true);
            renderer.end();
            if (gl) { gl.flush(); gl.finish(); }

            /* 8. Capture. Validate that the render actually contains the
                  skeleton -- not just a solid background color. We do this
                  by reading a few sample pixels and checking if ANY of them
                  differ from the background. A solid-color PNG means the
                  skeleton wasn't drawn (textures not uploaded yet, wrong
                  camera, etc.) -- retry up to 4 times before giving up. */
            var dataUrl = canvas.toDataURL('image/png');
            /* Quick pixel variance check: read 100 sample pixels; if all
               match the background, the skeleton wasn't drawn. */
            var hasContent = false;
            try {
              var ctx2d = canvas.getContext('2d');
              if (ctx2d) {
                var imgData = ctx2d.getImageData(0, 0, canvas.width, canvas.height);
                var d = imgData.data;
                var bgR = 18, bgG = 18, bgB = 26; /* #12121a */
                for (var pi = 0; pi < d.length; pi += 40) { /* sample every 10th pixel */
                  if (Math.abs(d[pi] - bgR) > 8 || Math.abs(d[pi+1] - bgG) > 8 || Math.abs(d[pi+2] - bgB) > 8) {
                    hasContent = true; break;
                  }
                }
              } else {
                /* 2D context unavailable (e.g. WebGL-only canvas) -- trust size */
                hasContent = dataUrl && dataUrl.length > 1500;
              }
            } catch(pxErr) {
              /* getImageData can fail on tainted canvases; fall back to size check */
              hasContent = dataUrl && dataUrl.length > 1500;
            }

            if (!hasContent) {
              /* Blank render -- retry this combo a few times (textures may
                 not be uploaded to GPU yet), then give up on this combo. */
              if (attempt < 4) {
                tryRender(attempt + 1);
                return;
              }
              throw new Error('blank canvas after ' + (attempt + 1) + ' attempts');
            }

            /* 9. Track the BEST render across all combos.
                  Some characters have multiple .skel/.atlas pairs where one
                  combo loads OK but renders only a tiny fragment of the
                  character (e.g. Elaine's combo 1 has bounds area ~74k, but
                  combo 4 has area ~3.3M -- 45x more visible content).
                  We can't tell from file size or name which is correct, so
                  we render every combo that loads successfully and pick the
                  one with the largest bounds area (most visible content). */
            var area = bSz.x * bSz.y;
            var comboDesc = combo.skel.name + ' + ' + combo.atlas.name;
            if (!bestRender || area > bestRender.area) {
              bestRender = { dataUrl: dataUrl, area: area, comboDesc: comboDesc, bounds: Math.round(bSz.x) + 'x' + Math.round(bSz.y) };
            }

            /* If this combo has very large bounds (>= 200,000 area, roughly
               450x450 or equivalent), it's almost certainly the correct
               render -- accept immediately without trying more combos. */
            var GOOD_ENOUGH_AREA = 200000;
            if (bestRender.area >= GOOD_ENOUGH_AREA) {
              saveBestRender();
              return;
            }

            /* Otherwise, try the next combo to see if it produces a better
               render. If there are no more combos, save the best we have. */
            try { p.dispose(); } catch(e2) {}
            tryNextCombo('have best area=' + Math.round(bestRender.area) + ', trying for better');
            return;
          } catch(e) {
            /* If the error message says "trying next combo", this combo
               was rejected due to small bounds -- fall through to the
               next combination instead of marking the character failed. */
            var msg = (e && e.message) ? e.message : String(e);
            if (msg.indexOf('trying next combo') !== -1 || msg.indexOf('trying for better') !== -1) {
              try { p.dispose(); } catch(e2) {}
              tryNextCombo(msg);
              return;
            }
            log('Error: ' + c.name + ' - ' + e, 'err');
            failed++; idx++; updateUI();
            try { p.dispose(); } catch(e2) {}
            container.style.cssText = '';
            setTimeout(processNext, 30);
          }
        }, wait);
      }
      tryRender(0);
    },
    error: function(p, reason) {
      /* This combination failed -- try the next one. */
      try { if (p) p.dispose(); } catch(e2) {}
      tryNextCombo(reason);
    }
    };
    cfg[isBinary ? 'binaryUrl' : 'jsonUrl'] = skelUrl;

    try { new spine.SpinePlayer(container, cfg); } catch(e) {
      tryNextCombo(e.message);
    }
  }
  tryNextCombo(null);
}

function startGen() {
  if (running) return;
  if (QUEUE.length === 0) { log('All characters already have thumbnails!', 'ok'); return; }
  running = true;
  idx = 0; done = 0; failed = 0;
  startTime = Date.now();
  startBtn.disabled = true;
  stopBtn.disabled = false;
  log('Starting thumbnail generation for ' + QUEUE.length + ' characters...');
  processNext();
}

function stopGen() {
  running = false;
  startBtn.disabled = false;
  stopBtn.disabled = true;
  log('Stopped at ' + done + ' / ' + QUEUE.length + '. Refresh page to resume (already-generated thumbs are skipped).');
  statusEl.textContent = 'Stopped. ' + done + ' done, ' + failed + ' failed.';
}'''

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Generate Thumbnails</title>
<link rel="stylesheet" href="https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/spine-player.css" onerror="this.href='spine/spine-player.css'">
<script src="https://unpkg.com/@esotericsoftware/spine-player@4.1.23/dist/iife/spine-player.js"></script>
<script>if(typeof spine==="undefined"){{var s=document.createElement("script");s.src="spine/spine-player.js";document.head.appendChild(s);}}</script>
<style>
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:'Segoe UI',system-ui,sans-serif; background:#0f0f13; color:#e0e0e0; display:flex; flex-direction:column; align-items:center; padding:30px 20px; min-height:100vh; }}
h1 {{ font-size:1.3rem; font-weight:600; color:#fff; margin-bottom:6px; }}
.sub {{ color:#666; font-size:.85rem; margin-bottom:24px; }}
#player-area {{ width:320px; height:320px; background:#12121a; border-radius:12px; overflow:hidden; border:1px solid #25252f; margin-bottom:20px; position:relative; }}
#player-area canvas {{ display:block; width:100%!important; height:100%!important; }}
.info {{ display:flex; gap:20px; margin-bottom:16px; flex-wrap:wrap; justify-content:center; }}
.info-box {{ background:#1a1a24; border:1px solid #25252f; border-radius:8px; padding:10px 20px; text-align:center; }}
.info-box .label {{ font-size:.7rem; color:#666; text-transform:uppercase; letter-spacing:.5px; }}
.info-box .value {{ font-size:1.1rem; font-weight:600; color:#fff; margin-top:2px; }}
#progress-bar-outer {{ width:500px; max-width:90vw; height:6px; background:#25252f; border-radius:3px; overflow:hidden; margin-bottom:8px; }}
#progress-bar-inner {{ height:100%; background:linear-gradient(90deg,#6c5ce7,#a29bfe); width:0%; transition:width 0.3s; border-radius:3px; }}
#status {{ font-size:.85rem; color:#888; margin-bottom:16px; text-align:center; }}
#current-name {{ font-size:.8rem; color:#555; margin-bottom:20px; text-align:center; max-width:500px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
.btns {{ display:flex; gap:10px; margin-bottom:20px; }}
.btn {{ padding:10px 28px; border-radius:8px; border:none; font-size:.9rem; font-weight:500; cursor:pointer; transition:all .2s; }}
.btn-start {{ background:#6c5ce7; color:#fff; }}
.btn-start:hover {{ background:#5a4bd1; }}
.btn-start:disabled {{ background:#333; color:#666; cursor:not-allowed; }}
.btn-stop {{ background:#e17055; color:#fff; }}
.btn-stop:hover {{ background:#d35f47; }}
.btn-stop:disabled {{ background:#333; color:#666; cursor:not-allowed; }}
#log {{ width:600px; max-width:90vw; height:200px; overflow-y:auto; background:#1a1a24; border:1px solid #25252f; border-radius:8px; padding:12px; font-size:.72rem; color:#555; font-family:'Consolas','Courier New',monospace; line-height:1.6; }}
#log .err {{ color:#e17055; }}
#log .ok {{ color:#00b894; }}
.speed-note {{ color:#555; font-size:.75rem; margin-top:10px; text-align:center; }}
</style>
</head>
<body>
<h1>Thumbnail Generator</h1>
<p class="sub">Renders each Spine character and saves thumb.png to its folder</p>

<div class="info">
  <div class="info-box"><div class="label">Total</div><div class="value" id="total-count">0</div></div>
  <div class="info-box"><div class="label">Done</div><div class="value" id="done-count">0</div></div>
  <div class="info-box"><div class="label">Failed</div><div class="value" id="fail-count">0</div></div>
  <div class="info-box"><div class="label">ETA</div><div class="value" id="eta">--</div></div>
</div>

<div id="player-area"></div>
<div id="progress-bar-outer"><div id="progress-bar-inner"></div></div>
<div id="status">Ready</div>
<div id="current-name"></div>

<div class="btns">
  <button class="btn btn-start" id="start-btn" onclick="startGen()">Start</button>
  <button class="btn btn-stop" id="stop-btn" onclick="stopGen()" disabled>Stop</button>
</div>

<div id="log"></div>
<p class="speed-note">Leave this tab open. Thumbnails are saved as thumb.png in each character folder.</p>

<script>
{js_code}
</script>
</body>
</html>"""
    return html

def write_thumb_server(root: Path):
    script = root / "generate_thumbs.py"
    script.write_text(
        '#!/usr/bin/env python3\n'
        '"""Thumbnail generator server. Saves thumb.png into character folders.\n\n'
        'Usage:  python generate_thumbs.py\n'
        'Then open http://localhost:8081/generate_thumbs.html\n'
        '"""\n'
        '\n'
        'import json, sys, base64, os\n'
        'from http.server import HTTPServer, SimpleHTTPRequestHandler\n'
        'from pathlib import Path\n'
        '\n'
        'ROOT = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")\n'
        '\n'
        'class Handler(SimpleHTTPRequestHandler):\n'
        '    def __init__(self, *a, **kw):\n'
        '        super().__init__(*a, directory=str(ROOT), **kw)\n'
        '\n'
        '    def do_POST(self):\n'
        '        if self.path == "/save_thumb":\n'
        '            length = int(self.headers.get("Content-Length", 0))\n'
        '            body = json.loads(self.rfile.read(length))\n'
        '            name = body["name"]\n'
        '            # `dir` is the relative path (forward slashes) of the\n'
        '            # folder where thumb.png should be written. With the\n'
        '            # Unity-native folder structure, this can be a deeply\n'
        '            # nested path like "AssetAddressable/Prefabs/LobbyUnit/\n'
        '            # Agravaine". We convert / to the OS separator so\n'
        '            # pathlib joins correctly on both Windows and POSIX.\n'
        '            folder = body.get("dir") or name\n'
        '            folder = folder.replace("/", os.sep)\n'
        '            data = base64.b64decode(body["data"])\n'
        '            dest_dir = ROOT / folder\n'
        '            dest_dir.mkdir(parents=True, exist_ok=True)\n'
        '            dest = dest_dir / "thumb.png"\n'
        '            dest.write_bytes(data)\n'
        '            self.send_response(200)\n'
        '            self.send_header("Content-Type", "application/json")\n'
        '            self.end_headers()\n'
        '            self.wfile.write(json.dumps({"ok": True}).encode())\n'
        '        else:\n'
        '            self.send_error(404)\n'
        '\n'
        '    def log_message(self, fmt, *args):\n'
        '        if "/save_thumb" in str(args[0]) if args else "":\n'
        '            return  # suppress per-thumb log spam\n'
        '        super().log_message(fmt, *args)\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    port = 8081\n'
        '    server = HTTPServer(("", port), Handler)\n'
        '    print(f"Thumbnail server on http://localhost:{port}")\n'
        '    print(f"Open http://localhost:{port}/generate_thumbs.html")\n'
        '    print(f"Serving from: {ROOT.resolve()}")\n'
        '    print("Press Ctrl+C to stop.")\n'
        '    try:\n'
        '        server.serve_forever()\n'
        '    except KeyboardInterrupt:\n'
        '        print("\\nStopped.")\n',
        encoding="utf-8",
    )
    print(f"  Created: {script}")


def write_thumb_bat(root: Path):
    bat = root / "generate_thumbs.bat"
    bat.write_text(
        "@echo off\n"
        'cd /d "%~dp0"\n'
        "echo Thumbnail Generator Server\n"
        "echo.\n"
        "start http://localhost:8081/generate_thumbs.html\n"
        "python generate_thumbs.py\n",
        encoding="utf-8",
    )
    print(f"  Created: {bat}")


def write_start_script(root: Path):
    bat = root / "start_server.bat"
    bat.write_text(
        "@echo off\n"
        'cd /d "%~dp0"\n'
        "echo Starting Spine Gallery server on http://localhost:8080\n"
        "echo Press Ctrl+C to stop.\n"
        "echo.\n"
        "start http://localhost:8080\n"
        "python -m http.server 8080\n",
        encoding="utf-8",
    )
    print(f"  Created: {bat}")


def main():
    if len(sys.argv) < 2:
        print("Usage: python build_gallery.py <path_to_Assets_folder>")
        print("  e.g.: python build_gallery.py \"C:/Users/denni/Desktop/LSDM/Extracted/Assets\"")
        sys.exit(1)

    root = Path(sys.argv[1])
    if not root.is_dir():
        print(f"Error: '{root}' is not a directory.")
        sys.exit(1)

    print(f"Scanning {root} recursively for Spine/texture/asset folders...")
    characters = scan_characters(root)
    media_count = sum(1 for c in characters if c["has_media"])
    non_media_count = len(characters) - media_count
    # Count entries per category so the user can see what was found.
    cat_counts = {}
    for c in characters:
        cat = c.get("category") or "(uncategorized)"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1
    print(f"Found {len(characters)} asset entries (recursively scanned).")
    print(f"  Entries with viewable media: {media_count}")
    if non_media_count > 0:
        print(f"  Non-media folders (hidden by default, use 'All Folders' to see): {non_media_count}")
    if cat_counts:
        print(f"  Categories found ({len(cat_counts)}):")
        for cat in sorted(cat_counts.keys(), key=lambda k: k.lower()):
            print(f"    {cat}: {cat_counts[cat]}")
    playable = sum(1 for c in characters if c["can_play"])
    with_thumbs = sum(1 for c in characters if c["has_thumb"])
    need_thumbs = playable - with_thumbs
    print(f"  Playable (skel+atlas): {playable}")
    print(f"  Already have thumbnails: {with_thumbs}")
    print(f"  Need thumbnails: {need_thumbs}")

    has_spine = download_spine_runtime(root)
    if has_spine:
        download_extra_libs(root)

    html = build_html(characters, root.name, has_spine)
    (root / "index.html").write_text(html, encoding="utf-8")
    print(f"  Created: {root / 'index.html'}")

    if has_spine:
        thumbs_html = build_generate_thumbs_html(characters, has_spine)
        if thumbs_html:
            (root / "generate_thumbs.html").write_text(thumbs_html, encoding="utf-8")
            print(f"  Created: {root / 'generate_thumbs.html'}")
        write_thumb_server(root)
        write_thumb_bat(root)

    write_start_script(root)
    print(f"\nDone!")
    if need_thumbs > 0:
        print(f"\n  >> {need_thumbs} characters need thumbnails.")
        print(f"  >> Double-click generate_thumbs.bat to generate them.")
        print(f"  >> Then double-click start_server.bat to browse the gallery.")
    else:
        print(f"  >> All characters have thumbnails!")
        print(f"  >> Double-click start_server.bat to browse the gallery.")


if __name__ == "__main__":
    main()
