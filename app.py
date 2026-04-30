from flask import Flask, request, jsonify, Response, stream_with_context
from flask_cors import CORS
import yt_dlp, os, tempfile, subprocess, sys, time

app = Flask(__name__)
CORS(app)

# Update yt-dlp on every cold start to avoid stale format errors
def update_ytdlp():
    try:
        subprocess.run([sys.executable, '-m', 'pip', 'install', '-q', '--upgrade', 'yt-dlp'],
                       capture_output=True, timeout=60)
    except Exception:
        pass

update_ytdlp()

YDL_BASE = {
    'quiet': True,
    'no_warnings': True,
    'retries': 5,
    'fragment_retries': 5,
    'socket_timeout': 30,
    # Bypass age/region restrictions
    'extractor_args': {
        'youtube': {
            'player_client': ['web', 'android'],
        }
    },
    'http_headers': {
        'User-Agent': 'Mozilla/5.0 (Linux; Android 11; Pixel 5) AppleWebKit/537.36 Chrome/90.0.4430.91 Mobile Safari/537.36',
    },
}

@app.route('/')
def index():
    return jsonify({'status': 'ok', 'service': 'ytflow-audio'})

@app.route('/info')
def info():
    video_id = request.args.get('id', '').strip()
    if not video_id:
        return jsonify({'error': 'Missing id'}), 400
    try:
        opts = {**YDL_BASE, 'skip_download': True}
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(f'https://www.youtube.com/watch?v={video_id}', download=False)
        return jsonify({
            'id': video_id,
            'title': data.get('title', ''),
            'channel': data.get('uploader') or data.get('channel', ''),
            'duration': int(data.get('duration') or 0),
            'thumb': data.get('thumbnail') or f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg',
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

@app.route('/audio')
def audio():
    video_id = request.args.get('id', '').strip()
    if not video_id:
        return jsonify({'error': 'Missing id'}), 400

    fd, tmp_base = tempfile.mkstemp()
    os.close(fd)
    os.unlink(tmp_base)

    # Try multiple format strategies
    format_strategies = [
        'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best',
        'bestaudio/best',
        '140/251/250/249/171/bestaudio/best',
        'worst',
    ]

    info_data = None
    actual = None

    for fmt in format_strategies:
        opts = {
            **YDL_BASE,
            'format': fmt,
            'outtmpl': tmp_base + '.%(ext)s',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'm4a',
                'preferredquality': '128',
            }],
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info_data = ydl.extract_info(
                    f'https://www.youtube.com/watch?v={video_id}', download=True)

            # Find output file
            for ext in ('m4a', 'mp3', 'mp4', 'webm', 'opus', 'ogg'):
                c = f'{tmp_base}.{ext}'
                if os.path.exists(c):
                    actual = c
                    break

            if not actual:
                base_dir = os.path.dirname(tmp_base)
                base_name = os.path.basename(tmp_base)
                for fname in sorted(os.listdir(base_dir)):
                    if fname.startswith(base_name):
                        actual = os.path.join(base_dir, fname)
                        break

            if actual and os.path.exists(actual):
                break  # Success

        except Exception:
            _cleanup(tmp_base)
            continue

    if not actual or not os.path.exists(actual):
        return jsonify({'error': 'No se pudo descargar el audio. El vídeo puede estar restringido.'}), 400

    file_ext = actual.rsplit('.', 1)[-1].lower()
    mime_map = {
        'm4a': 'audio/mp4', 'mp3': 'audio/mpeg',
        'opus': 'audio/ogg; codecs=opus', 'ogg': 'audio/ogg',
        'webm': 'audio/webm', 'mp4': 'audio/mp4',
    }
    mime = mime_map.get(file_ext, 'audio/mp4')
    file_size = os.path.getsize(actual)

    def safe_ascii(s):
        return str(s or '').encode('ascii', errors='replace').decode('ascii')

    title    = info_data.get('title', '') if info_data else video_id
    channel  = (info_data.get('uploader') or info_data.get('channel', '')) if info_data else ''
    duration = int(info_data.get('duration') or 0) if info_data else 0
    thumb    = (info_data.get('thumbnail') or f'https://i.ytimg.com/vi/{video_id}/mqdefault.jpg') if info_data else ''

    def stream_file():
        try:
            with open(actual, 'rb') as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    yield chunk
        finally:
            try: os.remove(actual)
            except OSError: pass

    return Response(
        stream_with_context(stream_file()),
        status=200,
        mimetype=mime,
        direct_passthrough=True,
        headers={
            'Content-Length': str(file_size),
            'Content-Type': mime,
            'Content-Disposition': 'inline',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'X-Title,X-Channel,X-Duration,X-Thumb,X-Ext',
            'X-Title':    safe_ascii(title),
            'X-Channel':  safe_ascii(channel),
            'X-Duration': str(duration),
            'X-Thumb':    safe_ascii(thumb),
            'X-Ext':      file_ext,
        }
    )

def _cleanup(tmp_base):
    for ext in ('m4a', 'mp3', 'mp4', 'webm', 'opus', 'ogg', ''):
        try:
            p = tmp_base + ('.' + ext if ext else '')
            if os.path.exists(p): os.remove(p)
        except OSError:
            pass

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8000))
    app.run(host='0.0.0.0', port=port, threaded=True)
