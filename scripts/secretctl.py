#!/usr/bin/env python3
"""Git-only Secret CRUD. Values never appear in command-line arguments.
Requires macOS/Linux, Python 3.10+ + PyYAML, SOPS and age. Does not call Kubernetes.
"""
from __future__ import annotations

import argparse
import base64
import contextlib
import fcntl
import getpass
import json
import os
from pathlib import Path
import re
import resource
import subprocess
import sys
import tempfile
import warnings

import yaml

ROOT = Path(__file__).resolve().parent.parent
SECRET_DIR = ROOT / "secrets"
NS_DIR = ROOT / "infrastructure/namespaces"
RESERVED_NS = {"default", "kube-system", "kube-public", "kube-node-lease", "flux-system"}
PROTECTED = {("flux-system", "sops-age"), ("flux-system", "flux-system")}
LABEL = r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?"
KEY = re.compile(r"[A-Za-z0-9._-]{1,253}\Z")
MAX_SIZE = 1024 * 1024


def require(ok: bool, message: str) -> None:
    if not ok:
        raise ValueError(message)


def load_yaml(raw: bytes | str) -> dict:
    # Do not print parser errors: they can include plaintext values.
    try:
        result = yaml.safe_load(raw)
    except yaml.YAMLError:
        raise ValueError("Invalid YAML; contents are suppressed.") from None
    require(isinstance(result, dict), "Expected one YAML object.")
    return result


def dump(obj: dict) -> bytes:
    return yaml.safe_dump(obj, sort_keys=False, allow_unicode=True).encode()


def run(args: list[str], *, data: bytes | None = None, env: dict | None = None) -> bytes:
    result = subprocess.run(args, input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, check=False)
    require(result.returncode == 0,
            f"{args[0]} failed; output suppressed to avoid secret disclosure.")
    return result.stdout


def relative(path: Path) -> str:
    require(not path.is_symlink(), "Symlinks are not allowed for managed files.")
    path.resolve().relative_to(ROOT)
    return path.relative_to(ROOT).as_posix()


def target(namespace: str, name: str) -> Path:
    require(len(namespace) <= 63 and re.fullmatch(LABEL, namespace) is not None,
            "Invalid namespace: use a lowercase DNS label.")
    require(len(name) <= 253 and all(len(x) <= 63 and re.fullmatch(LABEL, x)
                                   for x in name.split(".")), "Invalid Secret name.")
    require((namespace, name) not in PROTECTED,
            "This Secret belongs to bootstrap, not to the Git secrets layer.")
    path = SECRET_DIR / namespace / f"{name}.sops.yaml"
    relative(path)
    return path


def atomic_write(path: Path, raw: bytes) -> None:
    relative(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".encrypted-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def index_value(directory: Path, paths: list[Path]) -> dict:
    return {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
            "resources": [p.relative_to(directory).as_posix() for p in sorted(paths)]}


def secret_files() -> list[Path]:
    files = sorted(SECRET_DIR.glob("*/*.sops.yaml"))
    for path in SECRET_DIR.rglob("*.yaml"):
        require(path == SECRET_DIR / "kustomization.yaml" or path in files,
                f"Unexpected YAML in secrets/: {relative(path)}")
    return files


def namespace_files() -> list[Path]:
    return sorted(p for p in NS_DIR.glob("*.yaml") if p.name != "kustomization.yaml")


def ensure_namespace(namespace: str) -> None:
    if namespace in RESERVED_NS:
        return  # Kubernetes/Flux bootstrap owns these namespaces.
    path = NS_DIR / f"{namespace}.yaml"
    if not path.exists():
        atomic_write(path, dump({"apiVersion": "v1", "kind": "Namespace",
                                 "metadata": {"name": namespace}}))


def sync_indexes() -> None:
    atomic_write(SECRET_DIR / "kustomization.yaml", index_bytes(SECRET_DIR))
    atomic_write(NS_DIR / "kustomization.yaml", index_bytes(NS_DIR))


def index_bytes(directory: Path) -> bytes:
    files = secret_files() if directory == SECRET_DIR else namespace_files()
    return dump(index_value(directory, files))


def validate(obj: dict, path: Path, *, encrypted: bool) -> None:
    require(obj.get("apiVersion") == "v1" and obj.get("kind") == "Secret",
            "Only v1/Secret is supported.")
    meta = obj.get("metadata", {})
    require(isinstance(meta, dict), "Invalid metadata.")
    expected = target(str(meta.get("namespace", "")), str(meta.get("name", "")))
    require(expected == path, "Secret name/namespace must match its file path.")
    require("stringData" not in obj, "Use data; this helper handles base64 for you.")
    data = obj.get("data")
    require(isinstance(data, dict) and bool(data), "At least one data key is required.")
    require(all(isinstance(k, str) and KEY.fullmatch(k) for k in data), "Invalid data key.")
    require(not obj.get("immutable", False), "Immutable Secrets require a new name to rotate.")
    kind = obj.get("type", "Opaque")
    require(isinstance(kind, str) and bool(kind), "Invalid Secret type.")
    require(kind != "kubernetes.io/service-account-token",
            "Use short-lived TokenRequest tokens, not Git-managed service-account tokens.")
    if encrypted:
        require(isinstance(obj.get("sops"), dict), "SOPS metadata is missing.")
        require(all(isinstance(v, str) and v.startswith("ENC[AES256_GCM,") for v in data.values()),
                "An unencrypted data value was found.")
        return
    try:
        decoded = {k: base64.b64decode(v, validate=True) for k, v in data.items()}
    except (ValueError, TypeError):
        raise ValueError("Invalid base64 data; contents suppressed.") from None
    require(sum(map(len, decoded.values())) <= MAX_SIZE, "Secret exceeds 1 MiB.")
    required = {"kubernetes.io/tls": {"tls.crt", "tls.key"},
                "kubernetes.io/ssh-auth": {"ssh-privatekey"},
                "kubernetes.io/basic-auth": {"username", "password"},
                "kubernetes.io/dockerconfigjson": {".dockerconfigjson"}}
    require(required.get(kind, set()).issubset(data), "Required keys for this Secret type are missing.")
    if kind == "kubernetes.io/dockerconfigjson":
        try:
            conf = json.loads(decoded[".dockerconfigjson"])
        except (ValueError, UnicodeError):
            raise ValueError("Invalid Docker config JSON; contents suppressed.") from None
        require(isinstance(conf, dict) and isinstance(conf.get("auths"), dict) and bool(conf["auths"]),
                "Docker config needs actual auths; credential-helper references are insufficient.")


def encrypt(obj: dict, path: Path) -> bytes:
    validate(obj, path, encrypted=False)
    result = run(["sops", "--config", str(ROOT / ".sops.yaml"), "--encrypt",
                  "--filename-override", relative(path), "--input-type", "yaml",
                  "--output-type", "yaml", "/dev/stdin"], data=dump(obj))
    validate(load_yaml(result), path, encrypted=True)
    return result


def plaintext_guard() -> None:
    require(sys.platform in {"linux", "darwin"}, "Use macOS or Linux.")
    if sys.platform == "linux":
        require(Path("/proc/swaps").read_text().count("\n") <= 1,
                "Disable swap before handling plaintext on this Linux workstation.")
    # macOS: do not disable OS-managed swap or require Linux /proc and /dev/shm.
    # Use FileVault. Process memory is not guaranteed to stay out of swap/crash tooling.


def key_env(identity: bytes) -> dict:
    """Pass identity only to SOPS children, not via argv or a plaintext temp file."""
    require(b"AGE-SECRET-KEY-" in identity, "Invalid age identity.")
    env = os.environ.copy()
    env.pop("SOPS_AGE_KEY_FILE", None)
    env.pop("SOPS_AGE_KEY_CMD", None)
    env["SOPS_AGE_KEY"] = identity.decode("utf-8")
    return env


@contextlib.contextmanager
def identity_env():
    """Use an explicit/local identity, otherwise decrypt recovery into process memory."""
    env = os.environ.copy()
    configured = env.get("SOPS_AGE_KEY_FILE")
    if configured:
        key = Path(configured).expanduser()
        require(key.is_file(), "SOPS_AGE_KEY_FILE does not exist.")
        env["SOPS_AGE_KEY_FILE"] = str(key)
        yield env
        return
    if env.get("SOPS_AGE_KEY"):
        yield env
        return
    key = Path.home() / ".config/sops/age/home-infra.agekey"
    if key.is_file():
        env["SOPS_AGE_KEY_FILE"] = str(key)
        yield env
        return
    result = subprocess.run(["age", "--decrypt", str(ROOT / "bootstrap/sops-age.age")],
                            stdout=subprocess.PIPE, check=False)
    require(result.returncode == 0, "Could not unlock the recovery identity.")
    env = key_env(result.stdout)
    try:
        yield env
    finally:
        # References are released; Python cannot promise cryptographic memory erasure.
        env.pop("SOPS_AGE_KEY", None)


def changes(args) -> dict[str, str]:
    result: dict[str, str] = {}
    def put(key: str, raw: bytes) -> None:
        require(KEY.fullmatch(key) is not None and key not in result, "Invalid or duplicate data key.")
        result[key] = base64.b64encode(raw).decode("ascii")
    for key in args.prompt:
        require(KEY.fullmatch(key) is not None, "Invalid prompt key.")
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            first = getpass.getpass(f"{args.namespace}/{args.name} / {key}: ")
            second = getpass.getpass(f"Repeat {key}: ")
        require(bool(first) and first == second, "Empty value or entries do not match.")
        put(key, first.encode())
    for item in args.file:
        key, sep, filename = item.partition("=")
        require(bool(sep) and bool(filename), "Use --file KEY=/path/to/file.")
        src = Path(filename).expanduser().resolve()
        require(not src.is_relative_to(ROOT), "Source plaintext files must be outside this repository.")
        require(src.is_file() and src.stat().st_size <= MAX_SIZE, "Invalid or too large source file.")
        put(key, src.read_bytes())
    require(bool(result), "Specify --prompt KEY or --file KEY=/path.")
    return result


def check_indexes() -> list[Path]:
    files = secret_files()
    for directory in (SECRET_DIR, NS_DIR):
        p = directory / "kustomization.yaml"
        require(p.is_file() and load_yaml(p.read_bytes()) == load_yaml(index_bytes(directory)),
                f"Index differs: {relative(p)}; run secretctl.py index.")
    owned = set()
    for p in namespace_files():
        d = load_yaml(p.read_bytes())
        require(d.get("kind") == "Namespace" and d.get("apiVersion") == "v1",
                "Namespaces layer must contain only Namespaces.")
        ns = d.get("metadata", {}).get("name")
        require(ns == p.stem and ns not in RESERVED_NS, "Invalid namespace ownership.")
        owned.add(ns)
    for p in files:
        d = load_yaml(p.read_bytes())
        validate(d, p, encrypted=True)
        require(d["metadata"]["namespace"] in owned | RESERVED_NS,
                "A secret namespace is not declared.")
    return files


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="command", required=True)
    for cmd in ("add", "set", "delete"):
        c = sub.add_parser(cmd)
        c.add_argument("namespace")
        c.add_argument("name")
        if cmd != "delete":
            c.add_argument("--prompt", action="append", default=[], metavar="KEY")
            c.add_argument("--file", action="append", default=[], metavar="KEY=PATH")
        if cmd == "add":
            c.add_argument("--type", default="Opaque", dest="secret_type")
    for cmd in ("list", "index", "check"):
        c = sub.add_parser(cmd)
        if cmd == "check":
            c.add_argument("--decrypt", action="store_true")
    return p


def main() -> None:
    os.chdir(ROOT)
    os.umask(0o077)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    args = parser().parse_args()
    # RAM/plaintext precautions apply to commands that handle secret values.
    if args.command in {"add", "set"} or (args.command == "check" and args.decrypt):
        plaintext_guard()
    lock_path = Path(run(["git", "rev-parse", "--git-path", "secretctl.lock"]).decode().strip())
    with lock_path.open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == "index":
            for p in secret_files():
                d = load_yaml(p.read_bytes())
                validate(d, p, encrypted=True)
                ensure_namespace(d["metadata"]["namespace"])
            sync_indexes()
            print("Indexes updated. Review and commit the diff.")
        elif args.command in {"list", "check"}:
            files = check_indexes()
            if args.command == "list":
                for p in files:
                    d = load_yaml(p.read_bytes())
                    print(f"{d['metadata']['namespace']}/{d['metadata']['name']} "
                          f"[{d.get('type', 'Opaque')}] keys: {', '.join(d['data'])}")
            elif args.decrypt and files:
                with identity_env() as env:
                    for p in files:
                        d = load_yaml(run(["sops", "--decrypt", str(p)], env=env))
                        validate(d, p, encrypted=False)
            print(f"Checked {len(files)} encrypted Secrets; no values displayed.")
        else:
            path = target(args.namespace, args.name)
            if args.command == "delete":
                require(path.is_file(), "Secret does not exist.")
                answer = input(f"Type {args.namespace}/{args.name} to stage deletion in the worktree: ")
                require(answer == f"{args.namespace}/{args.name}", "Deletion cancelled.")
                path.unlink()
            else:
                if args.command == "add":
                    require(not path.exists(), "Secret exists; use set instead.")
                    obj = {"apiVersion": "v1", "kind": "Secret",
                           "metadata": {"name": args.name, "namespace": args.namespace},
                           "type": args.secret_type, "data": {}}
                else:
                    require(path.is_file(), "Secret does not exist; use add first.")
                    validate(load_yaml(path.read_bytes()), path, encrypted=True)
                    with identity_env() as env:
                        obj = load_yaml(run(["sops", "--decrypt", str(path)], env=env))
                    validate(obj, path, encrypted=False)
                obj["data"].update(changes(args))
                atomic_write(path, encrypt(obj, path))
                ensure_namespace(args.namespace)
            sync_indexes()
            print(f"Updated {relative(path)}; no commit/push or cluster changes performed.")


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, EOFError, getpass.GetPassWarning) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    except KeyboardInterrupt:
        print("Cancelled.", file=sys.stderr)
        sys.exit(130)
