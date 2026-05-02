from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp, os, tempfile, subprocess, sys, threading

app = Flask(__name__)
CORS(app, origins='*')

COOKIES_FILE = '/tmp/yt_cookies.txt'

def setup_cookies():
    """Write cookies from environment variable to file"""
    cookies = os.environ.get('YOUTUBE_COOKIES', '')
    if cookies:
        with open(COOKIES_FILE, 'w') as f:
            f.write(cookies)
        print(f"Cookies written: {len(cookies)} chars")
    else:
        print("No YOUTUBE_COOKIES env var found")

def update_ytdlp():
    try:
        subprocess.run([sys.executable,'-m','pip','install','-q','--upgrade','yt-dlp'],
                      capture_output=True, timeout=120)
        print("yt-dlp updated")
    except Exception as e:
        print(f"Update failed: {e}")

# Run on startup
setup_cookies()
threading.Thread(target=update_ytdlp, daemon=True).start()

def get_ydl_opts(tmp_path):
    opts = {
        'quiet': True,
        'no_warnings': True,
        'format': 'bestaudio/best',
        'outtmpl': tmp_path + '.%(ext)s',
        'retries': 3,
        'fragment_retries': 3,
        'socket_timeout': 30,
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': '128',
        }],
    }
    if os.path.exists(COOKIES_FILE):
        opts['cookiefile'] = COOKIES_FILE
    return opts

@app.route('/')
def index():
    has_cookies = os.path.exists(COOKIES_FILE)
    return jsonify({'status': 'ok', 'cookies': has_cookies})

@app.route('/audio')
def audio():
    vid = request.args.get('id', '').strip()
    if not vid:
        return jsonify({'error': 'Missing id'}), 400

    url = f'https://www.youtube.com/watch?v={vid}'
    fd, tmp = tempfile.mkstemp()
    os.close(fd)
    os.unlink(tmp)

    info = None
    out_file = None

    opts = get_ydl_opts(tmp)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
        for ext in ('mp3', 'm4a', 'webm', 'mp4', 'opus', 'ogg'):
            c = f'{tmp}.{ext}'
            if os.path.exists(c) and os.path.getsize(c) > 10000:
                out_file = c
                break
    except Exception as e:
        print(f"Download failed: {e}")
        _cleanup(tmp)

    if not out_file:
        return jsonify({'error': 'No se pudo descargar. Prueba con otro vídeo.'}), 400

    ext = out_file.rsplit('.', 1)[-1].lower()
    mime = {'mp3':'audio/mpeg','m4a':'audio/mp4','webm':'audio/webm','mp4':'audio/mp4'}.get(ext,'audio/mpeg')
    size = os.path.getsize(out_file)

    def safe(s):
        return str(s or '').encode('utf-8','replace').decode('ascii','replace')

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

    return Response(
        stream_with_context(stream_file()),
        status=200,
        mimetype=mime,
        headers={
            'Content-Length': str(size),
            'Content-Type': mime,
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'X-Title,X-Channel,X-Duration,X-Thumb,X-Ext',
            'X-Title':    safe(info.get('title','') if info else vid),
            'X-Channel':  safe((info.get('uploader') or info.get('channel','')) if info else ''),
            'X-Duration': str(int(info.get('duration') or 0) if info else 0),
            'X-Thumb':    safe((info.get('thumbnail') or f'https://i.ytimg.com/vi/{vid}/mqdefault.jpg') if info else ''),
            'X-Ext':      ext,
        }
    )

def _cleanup(tmp):
    for ext in ('mp3','m4a','mp4','webm','opus','ogg'):
        try:
            p = f'{tmp}.{ext}'
            if os.path.exists(p): os.remove(p)
        except: pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, threaded=True)
