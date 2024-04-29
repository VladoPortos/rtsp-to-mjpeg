from flask import Flask, Response, request, jsonify, render_template_string
import subprocess
import sqlite3
import os
from typing import Any, List, Optional, Tuple
from contextlib import contextmanager

app = Flask(__name__)
DATABASE = 'data/streams.db'


@contextmanager
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """
    Initialize the database to store stream configurations.

    This function creates a SQLite database file if it doesn't exist and
    creates a table named 'streams' to store stream configuration.

    Args:
        None

    Returns:
        None
    """
    os.makedirs(os.path.dirname(DATABASE), exist_ok=True)
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('''
        CREATE TABLE IF NOT EXISTS streams
        (id INTEGER PRIMARY KEY, url TEXT, active INTEGER, quality TEXT, resolution TEXT, fps INTEGER)
        ''')
        conn.commit()

# Call init_db to ensure the database is initialized on startup
init_db()

def query_db(query: str, args: Tuple[Any] = (), one: bool = False) -> Optional[List[Tuple[Any]]]:
    """
    Query the database and return a list of results or a single result.

    Args:
        query (str): The SQL query to execute.
        args (Tuple[Any], optional): The arguments to pass to the query. Defaults to ().
        one (bool, optional): Whether to return a single result or a list of results. Defaults to False.

    Returns:
        Optional[List[Tuple[Any]]]: A list of results or a single result, depending on the value of `one`.
    """
    with get_db_connection() as conn:
        cur = conn.cursor()
        cur.execute(query, args)
        rv = cur.fetchall()
        cur.close()
        return (rv[0] if rv else None) if one else rv

def add_stream(url: str, quality: str, resolution: str, fps: int) -> None:
    """
    Add a new stream to the database.

    Args:
        url (str): The URL of the stream.
        quality (str): The quality of the stream.
        resolution (str): The resolution of the stream.
        fps (int): The frames per second of the stream.

    Returns:
        None
    """
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('INSERT INTO streams (url, active, quality, resolution, fps) VALUES (?, 1, ?, ?, ?)',
                (url, quality, resolution, fps))
        conn.commit()
        conn.close()

def remove_stream(stream_id: int) -> None:
    """
    Remove a stream from the database.

    Args:
        stream_id (int): The ID of the stream to be removed.

    Returns:
        None
    """
    with get_db_connection() as conn:
        c = conn.cursor()
        c.execute('DELETE FROM streams WHERE id = ?', (stream_id,))
        conn.commit()
        conn.close()

def generate_frames(stream_id: int) -> bytes:
    """
    Generate frames from an RTSP stream.

    Args:
        stream_id (int): The ID of the stream.

    Yields:
        bytes: The frames in JPEG format.

    Raises:
        None

    Returns:
        None
    """
    stream = query_db('SELECT url, quality, resolution, fps FROM streams WHERE id = ?', [
                      stream_id], one=True)
    if not stream:
        return
    stream_url, quality, resolution, fps = stream

    command = [
        'ffmpeg',
        '-rtsp_transport', 'tcp',
        '-i', stream_url,
        '-r', str(fps),  # Frame rate
        '-c:v', 'mjpeg',
        '-vf', f'scale={resolution}',
        '-q:v', quality,
        '-f', 'image2pipe',
        '-'
    ]
    with subprocess.Popen(command, stdout=subprocess.PIPE, bufsize=-1) as p:
        data = b""
        try:
            while True:
                chunk = p.stdout.read(4096)
                if not chunk:
                    break
                data += chunk
                while True:
                    start = data.find(b'\xff\xd8')
                    end = data.find(b'\xff\xd9', start + 2)
                    if start != -1 and end != -1:
                        frame = data[start:end + 2]
                        data = data[end + 2:]
                        yield (b'--frame\r\n'
                               b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n')
                    else:
                        break
        finally:
            p.kill()


@app.route('/video_feed/<int:stream_id>')
def video_feed(stream_id: str) -> Response:
    """
    Video feed route to serve the MJPEG stream.

    Args:
        stream_id (str): The ID of the stream.

    Returns:
        Response: The response object containing the MJPEG stream.

    """
    return Response(generate_frames(stream_id),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


@app.route('/add_stream', methods=['POST'])
def add_stream_endpoint() -> Response:
    """
    API endpoint to add a new stream.

    Args:
        None

    Returns:
        Response: The response object indicating the success of the operation.
    """
    data: dict[str, Any] = request.json
    add_stream(data['url'], data['quality'], data['resolution'], data['fps'])
    return jsonify(success=True)


@app.route('/remove_stream/<int:stream_id>', methods=['DELETE'])
def remove_stream_endpoint(stream_id: int) -> Response:
    """
    API endpoint to remove a stream.

    Args:
        stream_id (int): The ID of the stream to be removed.

    Returns:
        Response: The response object indicating the success of the operation.
    """
    remove_stream(stream_id)
    return jsonify(success=True)


@app.route('/')
def index() -> str:
    """
    Main page to display available streams and management options.

    Returns:
        str: The HTML content of the main page.
    """
    streams = query_db('SELECT * FROM streams')
    return render_template_string('''
        <h1>Stream Manager</h1>
        <div>
            <form action="/add_stream" method="post" id="addForm">
                <input type="text" name="url" placeholder="RTSP URL" required />
                <input type="text" name="quality" placeholder="Quality (1-31)" required title="Enter a value from 1 to 31, where 1 is the highest quality and 31 is the lowest." />
                <input type="text" name="resolution" placeholder="Resolution (e.g., 640x480)" required />
                <input type="number" name="fps" placeholder="FPS (e.g., 15)" required />
                <button type="submit">Add Stream</button>
            </form>
        </div>
        <ul>
            {% for stream in streams %}
                <li>
                    <div>
                        <strong>Stream {{ stream[0] }}:</strong> {{ stream[1] }}
                        <a href="{{ url_for('video_feed', stream_id=stream[0]) }}">Watch Stream</a>
                        <button onclick="removeStream({{ stream[0] }})">Remove</button>
                    </div>
                </li>
            {% endfor %}
        </ul>
        <script>
            document.getElementById('addForm').onsubmit = function(e) {
                e.preventDefault();
                const formData = new FormData(this);
                fetch('/add_stream', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        url: formData.get('url'),
                        quality: formData.get('quality'),
                        resolution: formData.get('resolution'),
                        fps: formData.get('fps')
                    })
                }).then(response => response.json())
                .then(data => {
                    if (data.success) {
                        location.reload();
                    }
                });
            };
            function removeStream(id) {
                fetch('/remove_stream/' + id, { method: 'DELETE' })
                .then(response => response.json())
                .then(data => {
                    if (data.success) {
                        location.reload();
                    }
                });
            }
        </script>
    ''', streams=streams)


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000, debug=True)
