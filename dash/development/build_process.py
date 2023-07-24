import os
import sys
import json
import string
import shutil
import logging
import coloredlogs
import fire
import requests

from .._utils import run_command_with_process, compute_md5, job

logger = logging.getLogger(__name__)
coloredlogs.install(
    fmt="%(asctime)s,%(msecs)03d %(levelname)s - %(message)s", datefmt="%H:%M:%S"
)


class BuildProcess:
    def __init__(self, main, deps_info):
        self.logger = logger
        self.main = main
        self.build_folder = self._concat(self.main, "build")
        self.deps_info = deps_info
        self.npm_modules = self._concat(self.main, "node_modules")
        self.package_lock = self._concat(self.main, "package-lock.json")
        self.package = self._concat(self.main, "package.json")
        self._parse_package(path=self.package)
        self.asset_paths = (self.deps_folder, self.npm_modules)

    def _parse_package(self, path):
        with open(path, "r", encoding="utf-8") as fp:
            package = json.load(fp)
            self.version = package["version"]
            self.name = package["name"]
            self.deps_folder = self._concat(self.main, os.pardir, "deps")
            self.deps = package["dependencies"]

    @staticmethod
    def _concat(*paths):
        return os.path.realpath(os.path.sep.join((path for path in paths if path)))

    @staticmethod
    def _clean_path(path):
        if os.path.exists(path):
            logger.warning("🚨 %s already exists, remove it!", path)
            try:
                if os.path.isfile(path):
                    os.remove(path)
                if os.path.isdir(path):
                    shutil.rmtree(path)
            except OSError:
                sys.exit(1)
        else:
            logger.warning("🚨 %s doesn't exist, no action taken", path)

    @job("clean all the previous assets generated by build tool")
    def clean(self):
        for path in self.asset_paths:
            self._clean_path(path)

    @job("run `npm ci`")
    def npm(self):
        """Job to install npm packages."""
        os.chdir(self.main)
        run_command_with_process("npm ci")

    @job("build the renderer in dev mode")
    def watch(self):
        os.chdir(self.main)
        os.system("npm run build:dev")

    @job("run the whole building process in sequence")
    def build(self, build=None):
        self.clean()
        self.npm()
        self.bundles(build)
        self.digest()

    @job("compute the hash digest for assets")
    def digest(self):
        if not os.path.exists(self.deps_folder):
            try:
                os.makedirs(self.deps_folder)
            except OSError:
                logger.exception("🚨 having issues manipulating %s", self.deps_folder)
                sys.exit(1)

        payload = {self.name: self.version}

        for folder in (self.deps_folder, self.build_folder):
            copies = tuple(
                _
                for _ in os.listdir(folder)
                if os.path.splitext(_)[-1] in {".js", ".map"}
            )
            logger.info("bundles in %s %s", folder, copies)

            for copy in copies:
                payload[f"MD5 ({copy})"] = compute_md5(self._concat(folder, copy))

        with open(self._concat(self.main, "digest.json"), "w", encoding="utf-8") as fp:
            json.dump(payload, fp, sort_keys=True, indent=4, separators=(",", ":"))
        logger.info(
            "bundle digest in digest.json:\n%s",
            json.dumps(payload, sort_keys=True, indent=4),
        )

    @job("copy and generate the bundles")
    def bundles(self, build=None):  # pylint:disable=too-many-locals
        if not os.path.exists(self.deps_folder):
            try:
                os.makedirs(self.deps_folder)
            except OSError:
                logger.exception("🚨 having issues manipulating %s", self.deps_folder)
                sys.exit(1)

        self._parse_package(self.package_lock)

        getattr(self, "_bundles_extra", lambda: None)()

        versions = {
            "version": self.version,
            "package": self.name.replace(" ", "_").replace("-", "_"),
        }

        for scope, name, subfolder, filename, extras in self.deps_info:
            version = self.deps["/".join(filter(None, [scope, name]))]["version"]
            name_squashed = name.replace("-", "").replace(".", "")
            versions[name_squashed] = version

            logger.info("copy npm dependency => %s", filename)
            ext = "min.js" if "min" in filename.split(".") else "js"
            target = f"{name}@{version}.{ext}"

            shutil.copyfile(
                self._concat(self.npm_modules, scope, name, subfolder, filename),
                self._concat(self.deps_folder, target),
            )

            if extras:
                extras_str = '", "'.join(extras)
                versions[f"extra_{name_squashed}_versions"] = f'"{extras_str}"'

                for extra_version in extras:
                    url = f"https://unpkg.com/{name}@{extra_version}/umd/{filename}"
                    res = requests.get(url)
                    extra_target = f"{name}@{extra_version}.{ext}"
                    extra_path = self._concat(self.deps_folder, extra_target)
                    with open(extra_path, "wb") as fp:
                        fp.write(res.content)

        _script = "build:dev" if build == "local" else "build:js"
        logger.info("run `npm run %s`", _script)
        os.chdir(self.main)
        run_command_with_process(f"npm run {_script}")

        logger.info("generate the `__init__.py` from template and versions")
        with open(self._concat(self.main, "init.template"), encoding="utf-8") as fp:
            t = string.Template(fp.read())

        renderer_init = self._concat(self.deps_folder, os.pardir, "_dash_renderer.py")
        with open(renderer_init, "w", encoding="utf-8") as fp:
            fp.write(t.safe_substitute(versions))


class Renderer(BuildProcess):
    def __init__(self):
        """dash-renderer's path is binding with the dash folder hierarchy."""
        extras = ["18.2.0"]  # versions to include beyond what's in package.json
        super().__init__(
            self._concat(os.path.dirname(__file__), os.pardir, "dash-renderer"),
            (
                ("@babel", "polyfill", "dist", "polyfill.min.js", None),
                (None, "react", "umd", "react.production.min.js", extras),
                (None, "react", "umd", "react.development.js", extras),
                (None, "react-dom", "umd", "react-dom.production.min.js", extras),
                (None, "react-dom", "umd", "react-dom.development.js", extras),
                (None, "prop-types", None, "prop-types.min.js", None),
                (None, "prop-types", None, "prop-types.js", None),
            ),
        )


def renderer():
    fire.Fire(Renderer)