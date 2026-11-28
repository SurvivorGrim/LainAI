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
AGE_MAGIC = b"age-encryption.org/v1\n"


def _looks_sealed(path, min_size=0):
    """True if path is a non-empty age file (right header, plausible size). Cheap: no key needed."""
    try:
        if os.path.getsize(path) <= max(min_size, len(AGE_MAGIC)):
            return False
        with open(path, "rb") as f:
            return f.read(len(AGE_MAGIC)) == AGE_MAGIC
    except OSError:
        return False


def _flush_file(path):
    """Force a file's data onto the drive (not just the OS write cache)."""
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _flush_dir(path):
    """Force renames/new names in a folder onto the drive. Not possible (or needed) on Windows."""
    if IS_WIN:
        return
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


class VaultError(Exception):
    pass


class Vault:
    def __init__(self, root):
        self.root = root
        self.dir = os.path.join(root, "vault")
        self.id_file = os.path.join(self.dir, "identity.age")
        self.pub_file = os.path.join(self.dir, "recipient.txt")
        self.file = os.path.join(self.dir, "vault.age")
        self.bak = self.file + ".bak"
        self.new = self.file + ".new"
        self.recovered = None    # set by unlock() when vault.age was damaged and a copy was used instead
        self.main_bad = False    # vault.age didn't open - never rotate it over the good .bak
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
        # any sealed copy counts: if only vault.age is gone/damaged, "no vault" would mean making a NEW key,
        # and the old copies could never be opened again
        return os.path.exists(self.id_file) and any(os.path.exists(f) for f in (self.file, self.bak, self.new))

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
        _flush_file(self.id_file)
        _flush_file(self.pub_file)
        _flush_dir(self.dir)
        self._prepare_tmp()
        os.makedirs(self.files_dir(), exist_ok=True)
        os.makedirs(self.sessions_dir(), exist_ok=True)
        self.lock(keep_open=False)

    def unlock(self, passphrase):
        """Returns True on success, False on a wrong passphrase.
        If vault.age is damaged (e.g. empty after the drive was unplugged mid-save) the newest copy that
        opens is used instead and self.recovered says which one."""
        code, key, _ = self._age(["-d", "-j", "batchpass", self.id_file], passphrase=passphrase)
        if code:
            return False
        errors = []
        data = None
        self.recovered = None
        self.main_bad = False
        for path in (self.file, self.new, self.bak):   # .new is complete only if a save stopped between renames
            if not os.path.exists(path):
                continue
            if not _looks_sealed(path):
                errors.append("%s: %s" % (os.path.basename(path),
                                          "empty" if os.path.getsize(path) == 0 else "not an age file"))
                continue
            code, out, err = self._age(["-d", "-i", "-", path], data=key)
            if code == 0:
                try:
                    with tarfile.open(fileobj=io.BytesIO(out)):
                        pass
                except tarfile.TarError as e:
                    errors.append("%s: %s" % (os.path.basename(path), e))
                    continue
                data = out
                if path != self.file:
                    self.recovered = (path, os.path.getmtime(path), "; ".join(errors))
                    self.main_bad = True
                break
            errors.append("%s: %s" % (os.path.basename(path), err.splitlines()[0] if err else "failed"))
        del key
        if data is None:
            raise VaultError("vault could not be decrypted (your passphrase was right; the vault files are "
                             "damaged). Nothing was changed. Tried: " + "; ".join(errors or ["no vault files"]))
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
        """Seal the working folder into vault.age. Crash/unplug safe:
        1. encrypt to vault.age.new, check it, force it onto the drive
        2. old vault.age -> vault.age.bak (only if the old one is a real sealed vault)
        3. vault.age.new -> vault.age, force the renames onto the drive
        At every moment at least one complete copy exists on the drive."""
        if not os.path.isdir(self.work):
            return
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as t:
            t.add(self.work, arcname=".")
        plain = buf.getvalue()
        pub = open(self.pub_file).read().strip()
        try:
            os.remove(self.new)               # stale leftover from an interrupted save
        except OSError:
            pass
        code, _, err = self._age(["-r", pub, "-o", self.new], data=plain)
        if code:
            raise VaultError("encrypting failed (unlocked files left at %s): %s" % (self.work, err))
        # ciphertext is always bigger than the tar (header + 16 bytes per 64 KiB chunk)
        if not _looks_sealed(self.new, min_size=len(plain)):
            raise VaultError("the encrypted vault came out wrong - kept the previous one "
                             "(unlocked files left at %s)" % self.work)
        try:
            _flush_file(self.new)
            if os.path.exists(self.file) and not self.main_bad and _looks_sealed(self.file):
                os.replace(self.file, self.bak)
            # else: vault.age is missing/empty/damaged - keep the last good .bak instead of overwriting it
            os.replace(self.new, self.file)
            _flush_dir(self.dir)
        except OSError as e:
            raise VaultError("saving the vault to the drive failed (unlocked files left at %s): %s" % (self.work, e))
        self.main_bad = False
        self.recovered = None
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
        for f in (self.id_file, self.pub_file, self.file, self.bak, self.new):
            try:
                os.remove(f)
            except OSError:
                pass
        self.wipe_work()
