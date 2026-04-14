import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import musicbrainzngs
import requests
import json

# ---------------------------
# Paths
# ---------------------------

BASE = Path.home() / "music_rips"

FLAC_DIR = BASE / "FLAC"
ALAC_DIR = BASE / "ALAC"
AAC_DIR = BASE / "AAC"
WAV_DIR = BASE / "temp_wav"
CACHE_DIR = BASE / "metadata_cache"

for d in [FLAC_DIR, ALAC_DIR, AAC_DIR, WAV_DIR, CACHE_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ---------------------------
# MusicBrainz setup
# ---------------------------

musicbrainzngs.set_useragent(
    "cd_ripper",
    "1.0",
    "you@example.com"
)

# ---------------------------
# Helpers
# ---------------------------

def run(cmd, **kwargs):
    subprocess.run(cmd, check=True, **kwargs)

# ---------------------------
# Cache system
# ---------------------------

def cache_path(discid):
    return CACHE_DIR / f"{discid}.json"

def load_cache(discid):
    path = cache_path(discid)
    if path.exists():
        with open(path, "r") as f:
            return json.load(f)
    return None

def save_cache(discid, data):
    path = cache_path(discid)
    with open(path, "w") as f:
        json.dump(data, f, indent=2)

# ---------------------------
# Rip CD
# ---------------------------

def rip_cd():
    print("Ripping CD...")
    run(["cdparanoia", "-B", "-Z"], cwd=WAV_DIR)

# ---------------------------
# Cover art
# ---------------------------

def download_cover(release_id):
    url = f"https://coverartarchive.org/release/{release_id}/front"
    path = WAV_DIR / "cover.jpg"

    r = requests.get(url)
    if r.status_code == 200:
        path.write_bytes(r.content)
        return path

    return None

# ---------------------------
# Release selection
# ---------------------------

def choose_release(releases):

    print("\nMultiple releases found:\n")

    for i, r in enumerate(releases, start=1):
        artist = r["artist-credit"][0]["artist"]["name"]
        title = r["title"]
        date = r.get("date", "????")
        country = r.get("country", "??")

        print(f"{i}) {artist} - {title} ({date}) [{country}]")

    while True:
        choice = input("\nSelect release number: ")
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(releases):
                return releases[idx]
        print("Invalid choice.")

# ---------------------------
# Metadata lookup (with cache)
# ---------------------------

def get_cd_metadata():

    print("Reading disc ID...")

    result = subprocess.check_output(["cd-discid"]).decode().strip()
    parts = result.split()

    discid = parts[0]

    wav_files = sorted(WAV_DIR.glob("*.wav"))
    track_total = len(wav_files)

    # -----------------------
    # Check cache first
    # -----------------------
    cached = load_cache(discid)
    if cached:
        print("Loaded metadata from cache.")

        return (
            cached["artist"],
            cached["album"],
            cached["year"],
            1,
            1,
            cached["tracks"],
            None
        )

    releases = []

    # -----------------------
    # MusicBrainz lookup
    # -----------------------
    try:
        data = musicbrainzngs.get_releases_by_discid(
            discid,
            includes=["recordings", "artists"]
        )
        releases = data.get("disc", {}).get("release-list", [])
    except Exception as e:
        print("Disc ID lookup failed:", e)

    # fallback search (safe usage)
    if not releases:
        print("Falling back to search...")
        try:
            data = musicbrainzngs.search_releases(limit=10)
            releases = data.get("release-list", [])
        except Exception as e:
            print("Search failed:", e)

    # -----------------------
    # Manual fallback (and cache it)
    # -----------------------
    if not releases:
        print("\nNo metadata found. Enter manually.\n")

        artist = input("Artist: ")
        album = input("Album: ")
        year = input("Year: ")

        tracks = []
        for i in range(track_total):
            tracks.append(input(f"Track {i+1}: "))

        save_cache(discid, {
            "artist": artist,
            "album": album,
            "year": year,
            "tracks": tracks
        })

        return artist, album, year, 1, 1, tracks, None

    # -----------------------
    # Choose release
    # -----------------------
    release = releases[0] if len(releases) == 1 else choose_release(releases)

    artist = release["artist-credit"][0]["artist"]["name"]
    album = release["title"]
    year = release.get("date", "0000")[:4]
    release_id = release["id"]

    full = musicbrainzngs.get_release_by_id(
        release_id,
        includes=["recordings"]
    )

    mediums = full["release"]["medium-list"]
    medium = mediums[0]

    disc_number = int(medium.get("position", 1))
    disc_total = len(mediums)

    tracks = [t["recording"]["title"] for t in medium["track-list"]]

    print(f"\nSelected: {artist} - {album} ({year})\n")

    return artist, album, year, disc_number, disc_total, tracks, release_id

# ---------------------------
# Encoding
# ---------------------------

def encode_flac(wav, out, cover, meta):

    cmd = [
        "ffmpeg","-y",
        "-i", str(wav),
        "-c:a","flac",
        "-compression_level","8",
        "-threads","0"
    ]

    if cover:
        cmd.insert(2, "-i")
        cmd.insert(3, str(cover))
        cmd += ["-map","0:a","-map","1","-disposition:v","attached_pic"]

    for k,v in meta.items():
        cmd += ["-metadata", f"{k}={v}"]

    cmd.append(str(out))
    run(cmd)

def encode_alac(wav, out, meta):

    cmd = ["ffmpeg","-y","-i",str(wav),"-c:a","alac","-threads","0"]

    for k,v in meta.items():
        cmd += ["-metadata", f"{k}={v}"]

    cmd.append(str(out))
    run(cmd)

def encode_aac(wav, out, meta):

    cmd = [
        "ffmpeg","-y",
        "-i",str(wav),
        "-c:a","aac",
        "-b:a","256k",
        "-threads","0"
    ]

    for k,v in meta.items():
        cmd += ["-metadata", f"{k}={v}"]

    cmd.append(str(out))
    run(cmd)

# ---------------------------
# ReplayGain
# ---------------------------

def apply_replaygain(folder):
    flacs = list(folder.glob("*.flac"))
    if flacs:
        run(["metaflac", "--add-replay-gain", *map(str, flacs)])

# ---------------------------
# Processing
# ---------------------------

def process_album(artist, album, year, disc_number, disc_total, tracks, cover):

    album_folder = f"{artist}/{album} ({year})"

    flac_path = FLAC_DIR / album_folder
    alac_path = ALAC_DIR / album_folder
    aac_path = AAC_DIR / album_folder

    for p in [flac_path, alac_path, aac_path]:
        p.mkdir(parents=True, exist_ok=True)

    wav_files = sorted(WAV_DIR.glob("*.wav"))
    track_total = len(wav_files)

    for i, wav in enumerate(wav_files, start=1):

        title = tracks[i - 1] if i - 1 < len(tracks) else f"Track {i}"

        filename = f"d{disc_number:02} - t{i:02} {title} ({year})"

        meta = {
            "artist": artist,
            "album": album,
            "album_artist": artist,
            "title": title,
            "track": f"{i}/{track_total}",
            "disc": f"{disc_number}/{disc_total}",
            "date": year
        }

        with ThreadPoolExecutor(max_workers=3) as ex:
            ex.submit(encode_flac, wav, flac_path / f"{filename}.flac", cover, meta)
            ex.submit(encode_alac, wav, alac_path / f"{filename}.m4a", meta)
            ex.submit(encode_aac, wav, aac_path / f"{filename}.m4a", meta)

    apply_replaygain(flac_path)

# ---------------------------
# Main
# ---------------------------

if __name__ == "__main__":

    rip_cd()

    artist, album, year, disc_number, disc_total, tracks, release_id = get_cd_metadata()

    cover = download_cover(release_id) if release_id else None

    process_album(
        artist,
        album,
        year,
        disc_number,
        disc_total,
        tracks,
        cover
    )

    print("Done.")