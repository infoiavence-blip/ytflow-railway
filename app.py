from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp, os, tempfile, subprocess, sys, threading, requests as req
import json, time

app = Flask(__name__)
CORS(app, origins='*')

# Update yt-dlp on startup
def update_ytdlp():
    try:
        subprocess.run([sys.executable,'-m','pip','install','-q','--upgrade','yt-dlp','requests'],
                      capture_output=True, timeout=120)
        print("yt-dlp updated")
    except Exception as e:
        print(f"Update failed: {e}")

threading.Thread(target=update_ytdlp, daemon=True).start()

# Invidious public instances - these proxy YouTube so no IP blocking
INVIDIOUS = [
    'https://inv.nadeko.net',
    'https://invidious.nerdvpn.de',
    'https://invidious.privacyredirect.com',
    'https://invidious.fdn.fr',
    'https://yt.cdaut.de',
    'https://invidious.lunar.icu',
    'https://iv.melmac.space',
]

def get_audio_via_invidious(video_id):
    """Try each Invidious instance to get a direct audio URL"""
    for instance in INVIDIOUS:
        try:
            url = f"{instance}/api/v1/videos/{video_id}?fields=title,author,lengthSeconds,videoThumbnails,adaptiveFormats"
            r = req.get(url, timeout=10, headers={'User-Agent': 'Mozilla/5.0'})
            if r.status_code != 200:
                print(f"{instance}: HTTP {r.status_code}")
                continue
            
            data = r.json()
            if 'error' in data:
                print(f"{instance}: {data['error']}")
                continue

            # Find best audio format
            formats = data.get('adaptiveFormats', [])
            audio_formats = [f for f in formats if f.get('type','').startswith('audio/')]
            
            if not audio_formats:
                print(f"{instance}: no audio formats")
                continue

            # Sort by bitrate, take best
            audio_formats.sort(key=lambda x: x.get('bitrate', 0), reverse=True)
            best = audio_formats[0]
            audio_url = best.get('url')
            
            if not audio_url:
                # Try proxied URL
                audio_url = f"{instance}/latest_version?id={video_id}&itag={best.get('itag')}&local=true"
            
            print(f"{instance}: found audio url itag={best.get('itag')} type={best.get('type')}")
            
            return {
                'url': audio_url,
                'title': data.get('title', video_id),
                'channel': data.get('author', ''),
                'duration': data.get('lengthSeconds', 0),
                'thumb': data.get('videoThumbnails', [{}])[0].get('url', f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg'),
                'instance': instance,
                'itag': best.get('itag'),
                'mime': best.get('type','audio/webm').split(';')[0],
            }
        except Exception as e:
            print(f"{instance}: {e}")
            continue
    return None

def get_audio_via_ytdlp(video_id):
    """Fallback: try yt-dlp with various bypass strategies"""
    url = f'https://www.youtube.com/watch?v={video_id}'
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    os.unlink(tmp)

    strategies = [
        # Strategy 1: Android client (most likely to work)
        {
            'format': 'bestaudio/best',
            'extractor_args': {'youtube': {
                'player_client': ['android'],
                'player_skip': ['webpage', 'configs'],
            }},
            'http_headers': {
                'User-Agent': 'com.google.android.youtube/19.09.37 (Linux; U; Android 11) gzip',
            },
        },
        # Strategy 2: iOS client
        {
            'format': 'bestaudio/best',
            'extractor_args': {'youtube': {
                'player_client': ['ios'],
            }},
            'http_headers': {
                'User-Agent': 'com.google.ios.youtube/19.09.3 (iPhone14,3; U; CPU iOS 15_6 like Mac OS X)',
            },
        },
        # Strategy 3: TV client (often less restricted)
        {
            'format': 'bestaudio/best',
            'extractor_args': {'youtube': {
                'player_client': ['tv_embedded'],
            }},
        },
    ]

    for i, extra in enumerate(strategies):
        opts = {
            'quiet': True,
            'no_warnings': True,
            'outtmpl': tmp + '.%(ext)s',
            'retries': 2,
            'fragment_retries': 2,
            'socket_timeout': 20,
            'postprocessors': [{'key': 'FFmpegExtractAudio', 'preferredcodec': 'mp3', 'preferredquality': '128'}],
            **extra,
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True)
            for ext in ('mp3','m4a','webm','mp4','opus','ogg'):
                c = f'{tmp}.{ext}'
                if os.path.exists(c) and os.path.getsize(c) > 10000:
                    return {'file': c, 'info': info, 'ext': ext}
        except Exception as e:
            print(f"yt-dlp strategy {i}: {e}")
            _cleanup(tmp)
    return None

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'service': 'tubefy-audio-v4'})

@app.route('/audio')
def audio():
    vid = request.args.get('id', '').strip()
    if not vid:
        return jsonify({'error': 'Missing id'}), 400

    print(f"[{vid}] Attempting download...")

    # Strategy 1: Invidious API (no IP blocking)
    inv = get_audio_via_invidious(vid)
    if inv:
        print(f"[{vid}] Invidious success from {inv['instance']}")
        # Stream the audio URL through our server
        try:
            audio_url = inv['url']
            r = req.get(audio_url, stream=True, timeout=30, headers={
                'User-Agent': 'Mozilla/5.0',
                'Referer': inv['instance'],
            })
            if r.status_code == 200:
                ct = r.headers.get('content-type', inv['mime'])
                # Determine extension
                if 'mp4' in ct or 'm4a' in ct:
                    ext = 'm4a'
                    mime = 'audio/mp4'
                elif 'webm' in ct or 'opus' in ct:
                    ext = 'webm'
                    mime = 'audio/webm'
                else:
                    ext = 'm4a'
                    mime = 'audio/mp4'

                def stream_response():
                    for chunk in r.iter_content(65536):
                        yield chunk

                return Response(
                    stream_with_context(stream_response()),
                    status=200,
                    mimetype=mime,
                    headers={
                        'Content-Type': mime,
                        'Access-Control-Allow-Origin': '*',
                        'Access-Control-Expose-Headers': 'X-Title,X-Channel,X-Duration,X-Thumb,X-Ext',
                        'X-Title': safe(inv['title']),
                        'X-Channel': safe(inv['channel']),
                        'X-Duration': str(inv['duration']),
                        'X-Thumb': safe(inv['thumb']),
                        'X-Ext': ext,
                    }
                )
            else:
                print(f"[{vid}] Invidious stream failed: {r.status_code}, trying proxied URL")
                # Try proxied URL through instance
                proxy_url = f"{inv['instance']}/latest_version?id={vid}&itag={inv['itag']}&local=true"
                r2 = req.get(proxy_url, stream=True, timeout=30, headers={'User-Agent': 'Mozilla/5.0'})
                if r2.status_code == 200:
                    def stream2():
                        for chunk in r2.iter_content(65536):
                            yield chunk
                    return Response(stream_with_context(stream2()), status=200, mimetype='audio/mp4',
                        headers={'Content-Type':'audio/mp4','Access-Control-Allow-Origin':'*',
                                 'Access-Control-Expose-Headers':'X-Title,X-Channel,X-Duration,X-Thumb,X-Ext',
                                 'X-Title':safe(inv['title']),'X-Channel':safe(inv['channel']),
                                 'X-Duration':str(inv['duration']),'X-Thumb':safe(inv['thumb']),'X-Ext':'m4a'})
        except Exception as e:
            print(f"[{vid}] Invidious stream error: {e}")

    # Strategy 2: yt-dlp fallback
    print(f"[{vid}] Trying yt-dlp fallback...")
    result = get_audio_via_ytdlp(vid)
    if result:
        out_file = result['file']
        info = result['info']
        ext = result['ext']
        size = os.path.getsize(out_file)
        mime = {'mp3':'audio/mpeg','m4a':'audio/mp4','webm':'audio/webm'}.get(ext,'audio/mpeg')

        def stream_file():
            try:
                with open(out_file, 'rb') as f:
                    while True:
                        chunk = f.read(65536)
                        if not chunk: break
                        yield chunk
            finally:
                try: os.remove(out_file)
                except: pass

        return Response(stream_with_context(stream_file()), status=200, mimetype=mime,
            headers={
                'Content-Length': str(size),
                'Content-Type': mime,
                'Access-Control-Allow-Origin': '*',
                'Access-Control-Expose-Headers': 'X-Title,X-Channel,X-Duration,X-Thumb,X-Ext',
                'X-Title': safe(info.get('title','')),
                'X-Channel': safe(info.get('uploader') or info.get('channel','')),
                'X-Duration': str(int(info.get('duration') or 0)),
                'X-Thumb': safe(info.get('thumbnail') or f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg'),
                'X-Ext': ext,
            })

    return jsonify({'error': 'No se pudo descargar. Prueba con otro vídeo.'}), 400

def safe(s):
    return str(s or '').encode('utf-8','replace').decode('ascii','replace')

def _cleanup(tmp):
    for ext in ('mp3','m4a','mp4','webm','opus','ogg'):
        try:
            p = f'{tmp}.{ext}'
            if os.path.exists(p): os.remove(p)
        except: pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, threaded=True)
