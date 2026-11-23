"""Generic helpers for running commands inside containers via the Docker API.

Shared by bench CLI orchestration (``sites.bench_exec``) and nginx config
reloads (``sites.nginx_config``) so there is exactly one place that knows how
to stream a docker-exec's output.
"""
import io
import tarfile
import time
import zipfile

import docker

_client = None


def get_client():
    """Return a lazily-created, process-wide docker-py client."""
    global _client
    if _client is None:
        _client = docker.from_env()
    return _client


def exec_stream(container_name, command, *, user=None, workdir=None, on_output=None):
    """Run ``command`` inside ``container_name``, streaming its output.

    :param container_name: Name of the running container to exec into.
    :type container_name: str

    :param command: Command to run, as a list of args.
    :type command: list

    :param on_output: Optional callback invoked with each chunk of decoded
        output as it arrives.
    :type on_output: callable

    :returns: The command's exit code.
    :rtype: int
    """
    client = get_client()
    container = client.containers.get(container_name)

    exec_id = client.api.exec_create(
        container.id,
        command,
        user=user or '',
        workdir=workdir,
        stdout=True,
        stderr=True,
    )['Id']

    stream = client.api.exec_start(exec_id, stream=True)
    for chunk in stream:
        if not chunk:
            continue
        if on_output:
            on_output(chunk.decode('utf-8', errors='replace'))

    return client.api.exec_inspect(exec_id).get('ExitCode', 1)


def tail_container_logs(container_name, since):
    """Fetch a container's own stdout/stderr emitted after ``since``.

    Used to surface a container's *main process* output (e.g. a bench
    container's entrypoint running ``bench init``) rather than an exec'd
    command's — there's no exec to stream in that case, just the
    container's own log stream polled incrementally.

    :param since: Unix timestamp (as returned by a previous call, or
        ``time.time()`` for the first call) to fetch logs after.
    :returns: ``(next_since, text)`` — pass ``next_since`` back in on the
        next call to avoid re-fetching the same lines.
    :rtype: (float, str)
    """
    client = get_client()
    container = client.containers.get(container_name)
    next_since = time.time()
    chunk = container.logs(stdout=True, stderr=True, since=since, until=next_since)
    return next_since, chunk.decode('utf-8', errors='replace')


def run_command(container_name, command, **kwargs):
    """Run a command inside a container, collecting all output into a string.

    Convenience wrapper over :func:`exec_stream` for callers that don't need
    incremental streaming (e.g. health polling).

    :rtype: (int, str)
    """
    collected = []
    exit_code = exec_stream(
        container_name, command, on_output=collected.append, **kwargs
    )
    return exit_code, ''.join(collected)


def _build_tar(entries):
    """Build an in-memory tar from an iterable of ``(name, data)`` pairs."""
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode='w') as tar:
        for name, data in entries:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mtime = time.time()
            tar.addfile(info, io.BytesIO(data))
    return tar_stream.getvalue()


def _put_archive(container_name, dest_dir, tar_bytes):
    """Extract a tar into ``dest_dir`` (which must already exist) and fix ownership.

    Docker's archive API always extracts as root regardless of which user
    the container itself runs as, so ownership is fixed up afterwards.
    """
    client = get_client()
    container = client.containers.get(container_name)
    container.put_archive(dest_dir, tar_bytes)
    run_command(container_name, ['chown', '-R', 'frappe:frappe', dest_dir], user='root')


def put_bytes(container_name, dest_dir, filename, data):
    """Write ``data`` as a file named ``filename`` inside ``dest_dir``.

    Used to get an uploaded file (from an HTTP request) into a bench
    container without a shared filesystem — there's no other path between
    the two.
    """
    _put_archive(container_name, dest_dir, _build_tar([(filename, data)]))


def put_zip_contents(container_name, dest_dir, zip_bytes, transform_name=None):
    """Extract an uploaded zip's contents into ``dest_dir`` inside the container.

    Existing files with the same name are overwritten; files already
    present but not in the zip are left alone (this is a merge, not a
    wipe-and-replace).

    :param transform_name: Optional callable applied to each entry's path
        before writing it, e.g. to strip a prefix. Returning a falsy
        value skips that entry.
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
        entries = []
        for name in zip_file.namelist():
            if name.endswith('/'):
                continue
            out_name = transform_name(name) if transform_name else name
            if out_name:
                entries.append((out_name, zip_file.read(name)))
    _put_archive(container_name, dest_dir, _build_tar(entries))


def put_tar_contents(container_name, dest_dir, tar_bytes, transform_name=None):
    """Extract an uploaded tar's contents into ``dest_dir`` inside the container.

    Accepts plain or compressed (gzip/bz2/xz) tar — ``tarfile.open``'s
    default read mode auto-detects compression, but Docker's archive API
    needs an uncompressed stream to extract, so this always re-wraps into
    a fresh plain tar rather than passing the upload straight through.
    Same merge semantics and ``transform_name`` hook as ``put_zip_contents``.
    """
    with tarfile.open(fileobj=io.BytesIO(tar_bytes)) as source_tar:
        entries = []
        for member in source_tar.getmembers():
            if not member.isfile():
                continue
            out_name = transform_name(member.name) if transform_name else member.name
            if out_name:
                entries.append((out_name, source_tar.extractfile(member).read()))
    _put_archive(container_name, dest_dir, _build_tar(entries))
