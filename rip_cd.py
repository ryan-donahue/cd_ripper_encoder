import subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import musicbrainzngs
import requests

BASE = Path("~/music_rips")

FLAC_DIR = BASE / "FLAC"
ALAC_DIR = BASE / "ALAC"
AAC_DIR = BASE / "AAC"
WAV_DIR = BASE / "temp_wav"

for d in [FLAC_DIR, ALAC_DIR, AAC_DIR, WAV_DIR]:
    d.mkdir(parents=True, exist_ok=True)

musicbrainzngs.set_useragent("cd_ripper", "1.0", "you@example.com")

# ---------------------------
# Helpers
# ---------------------------

def run(cmd, **kwargs):
    subprocess.run(cmd, check=True, **kwargs)

# ---------------------------
# Step 1: Rip CD
# ---------------------------

def rip_cd():
    print("Ripping CD...")
    run(["cdparanoia", "-Z", "-B"], cwd=WAV_DIR)

# ---------------------------
# Step 2: Metadata
# ---------------------------

def get_cd_metadata():

    discid_output = subprocess.check_output(["cd-discid"]).decode()
    discid = discid_output.split()[0]

    data = musicbrainzngs.get_releases_by_discid(
        discid,
        includes=["recordings", "artists"]
    )

    release = data["disc"]["release-list"][0]

    artist = release["artist-credit"][0]["artist"]["name"]
    album = release["title"]
    year = release["date"][:4]

    mediums = release["medium-list"]

    # detect which disc we're ripping
    medium = mediums[0]
    disc_number = int(medium["position"])
    disc_total = len(mediums)

    tracks = [t["recording"]["title"] for t in medium["track-list"]]

    return artist, album, year, disc_number, disc_total, tracks, release["id"]

# ---------------------------
# Step 3: Cover Art
# ---------------------------

def download_cover(release_id):

    url = f"https://coverartarchive.org/release/{release_id}/front"
    path = WAV_DIR / "cover.jpg"

    r = requests.get(url)

    if r.status_code == 200:
        with open(path, "wb") as f:
            f.write(r.content)

    return path

# ---------------------------
# Encoding Functions
# ---------------------------

def encode_flac(wav, out, cover, meta):

    cmd = [
        "ffmpeg","-y",
        "-i", str(wav),
        "-i", str(cover),
        "-map","0:a",
        "-map","1",
        "-c:a","flac",
        "-compression_level","8",
        "-threads","0",
        "-disposition:v","attached_pic"
    ]

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
    subprocess.run(
        f"metaflac --add-replay-gain {folder}/*.flac",
        shell=True
    )

# ---------------------------
# Main Processing
# ---------------------------

def process_album(artist, album, year, disc_number, disc_total, tracks, cover):

    album_folder = f"{artist}/{album} ({year})"

    flac_path = FLAC_DIR / album_folder
    alac_path = ALAC_DIR / album_folder
    aac_path = AAC_DIR / album_folder

    for p in [flac_path, alac_path, aac_path]:
        p.mkdir(parents=True, exist_ok=True)

    track_total = len(tracks)

    for i, title in enumerate(tracks, start=1):

        wav = WAV_DIR / f"track{i:02}.cdda.wav"

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

        with ThreadPoolExecutor(max_workers=3) as executor:

            executor.submit(
                encode_flac,
                wav,
                flac_path / f"{filename}.flac",
                cover,
                meta
            )

            executor.submit(
                encode_alac,
                wav,
                alac_path / f"{filename}.m4a",
                meta
            )

            executor.submit(
                encode_aac,
                wav,
                aac_path / f"{filename}.m4a",
                meta
            )

    apply_replaygain(flac_path)

# ---------------------------
# Run
# ---------------------------

if __name__ == "__main__":

    rip_cd()

    artist, album, year, disc_number, disc_total, tracks, release_id = get_cd_metadata()

    cover = download_cover(release_id)

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