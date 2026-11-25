"""Encrypted vault: your files + conversations, sealed with age.

identity.age  - the vault's private key, encrypted with your passphrase (scrypt)
recipient.txt - the matching public key, so Lain can re-lock without asking again
vault.age     - a tar of the vault folder, encrypted to that key (X25519 + ChaCha20-Poly1305)

Plaintext only ever exists in the unlocked working folder (RAM on Linux) while Lain runs.
The passphrase is passed only to the age process that needs it and is never stored.
"""
import io
import os
import shutil
import stat
import subprocess
import tarfile
import tempfile

IS_WIN = os.name == "nt"


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, root):
        self.root = root
        self.dir = os.path.join(root, "vault")
        self.id_file = os.path.join(self.dir, "identity.age")
        self.pub_file = os.path.join(self.dir, "recipient.txt")
        self.file = os.path.join(self.dir, "vault.age")
        if IS_WIN:
            base = os.path.join(tempfile.gettempdir(), "LainAI")
        else:
            base = "/dev/shm" if os.access("/dev/shm", os.W_OK) else (os.environ.get("XDG_RUNTIME_DIR") or tempfile.gettempdir())
            base = os.path.join(base, "lainai-%d" % os.getuid())
        self.tmp = base                          # private scratch area (RAM on Linux)
        self.work = os.path.join(base, "vault")  # unlocked vault contents
        self._prepare_tmp()
        bindir = os.path.join(root, "bin", "win" if IS_WIN else "linux")
        self.bindir = self._runnable(bindir)
        ext = ".exe" if IS_WIN else ""
        self.age = os.path.join(self.bindir, "age" + ext)
        self.keygen = os.path.join(self.bindir, "age-keygen" + ext)

    # exFAT mounts sometimes refuse execution; fall back to a private RAM copy
    def _runnable(self, d):
        if IS_WIN or os.access(os.path.join(d, "age"), os.X_OK):
            return d
        cp = os.path.join(self.tmp, "bin")
        os.makedirs(cp, exist_ok=True)
        for f in os.listdir(d):
            shutil.copy2(os.path.join(d, f), cp)
            os.chmod(os.path.join(cp, f), 0o700)
        return cp

    def _age(self, args, data=None, passphrase=None):
        env = dict(os.environ)
        env["PATH"] = self.bindir + os.pathsep + env.get("PATH", "")   # so age finds age-plugin-batchpass
        if passphrase is not None:
            env["AGE_PASSPHRASE"] = passphrase
        try:
            p = subprocess.run([self.age] + args, input=data, capture_output=True, env=env,
                               creationflags=0x08000000 if IS_WIN else 0)   # CREATE_NO_WINDOW
        except OSError as e:
            raise VaultError("couldn't run the encryption tool (age): %s" % e)
        return p.returncode, p.stdout, p.stderr.decode("utf-8", "replace").strip()

    # ---- state ----
    def exists(self):
        return os.path.exists(self.id_file) and os.path.exists(self.file)

    def leftover(self):
        return os.path.isdir(self.work)

    def files_dir(self):
        return os.path.join(self.work, "files")

    def sessions_dir(self):
        return os.path.join(self.work, "sessions")

    def _prepare_tmp(self):
        os.makedirs(self.tmp, mode=0o700, exist_ok=True)
        if not IS_WIN:
            st = os.lstat(self.tmp)
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
                raise VaultError("%s is not a private folder owned by you - refusing to use it" % self.tmp)
            os.chmod(self.tmp, 0o700)

    # ---- create / unlock / lock / reset ----
    def create(self, passphrase):
        os.makedirs(self.dir, exist_ok=True)
        key = subprocess.run([self.keygen], capture_output=True,
                             creationflags=0x08000000 if IS_WIN else 0).stdout       # stays in memory
        pub = subprocess.run([self.keygen, "-y"], input=key, capture_output=True,
                             creationflags=0x08000000 if IS_WIN else 0).stdout.decode().strip()
        if not pub.startswith("age1"):
            raise VaultError("could not generate the vault key")
        code, _, err = self._age(["-e", "-j", "batchpass", "-o", self.id_file], data=key, passphrase=passphrase)
        del key
        if code:
            raise VaultError("could not protect the vault key: " + err)
        with open(self.pub_file, "w", newline="\n") as f:
            f.write(pub + "\n")
        self._prepare_tmp()
        os.makedirs(self.files_dir(), exist_ok=True)
        os.makedirs(self.sessions_dir(), exist_ok=True)
        self.lock(keep_open=False)

    def unlock(self, passphrase):
        """Returns True on success, False on a wrong passphrase."""
        code, key, _ = self._age(["-d", "-j", "batchpass", self.id_file], passphrase=passphrase)
        if code:
            return False
        code, data, err = self._age(["-d", "-i", "-", self.file], data=key)
        del key
        if code:
            raise VaultError("vault could not be decrypted: " + err)
        self._prepare_tmp()
        if os.path.isdir(self.work):
            shutil.rmtree(self.work)
        os.makedirs(self.work)
        with tarfile.open(fileobj=io.BytesIO(data)) as t:
            for m in t.getmembers():   # refuse anything that would escape the vault folder
                dest = os.path.realpath(os.path.join(self.work, m.name))
                if not (dest + os.sep).startswith(os.path.realpath(self.work) + os.sep) or m.issym() or m.islnk() or m.isdev():
                    continue
                t.extract(m, self.work, **({"filter": "data"} if hasattr(tarfile, "data_filter") else {}))
        os.makedirs(self.files_dir(), exist_ok=True)
        os.makedirs(self.sessions_dir(), exist_ok=True)
        return True

    def size(self):
        total = 0
        for dp, _, fs in os.walk(self.work):
            for f in fs:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    pass
        return total

    def lock(self, keep_open=False):
        if not os.path.isdir(self.work):
            return
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            t.add(self.work, arcname=".")
        pub = open(self.pub_file).read().strip()
        new = self.file + ".new"
        code, _, err = self._age(["-r", pub, "-o", new], data=buf.getvalue())
        if code:
            raise VaultError("encrypting failed (unlocked files left at %s): %s" % (self.work, err))
        if os.path.exists(self.file):
            os.replace(self.file, self.file + ".bak")
        os.replace(new, self.file)
        if not keep_open:
            self.wipe_work()

    def wipe_work(self):
        def onerr(fn, path, _):
            try:
                os.chmod(path, stat.S_IWRITE)
                fn(path)
            except OSError:
                pass
        if os.path.isdir(self.tmp):
            shutil.rmtree(self.tmp, onerror=onerr)

    def reset(self):
        for f in (self.id_file, self.pub_file, self.file, self.file + ".bak", self.file + ".new"):
            try:
                os.remove(f)
            except OSError:
                pass
        self.wipe_work()
