#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Java EXE Packager - GUI Tkinter / CLI, Windows, JDK jpackage.

Does not claim to compile every Java project automatically. Maven/Gradle must
be able to build the project, and external/native dependencies must be packaged.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path, PurePosixPath
from typing import Callable, Optional

DEFAULT_MODULES = "javafx.controls,javafx.fxml"
JAVA_SOURCE_RE = re.compile(r"\bpublic\s+static\s+void\s+main\s*\(\s*(?:final\s+)?(?:java\.lang\.)?String\s*(?:\[\s*\]\s*\w+|\.\.\.\s*\w+|\w+\s*\[\s*\])", re.S)
PACKAGE_RE = re.compile(r"^\s*package\s+([\w.]+)\s*;", re.M)
CLASS_RE = re.compile(r"\b(?:public\s+)?(?:final\s+)?(?:class|record|enum)\s+(\w+)")
EXCLUDED_JAR_MARKERS = ("-sources.jar", "-javadoc.jar", "-tests.jar", "-test.jar", "-plain.jar", ".jar.original")
EXTRA_RESOURCES = ("assets", "resources", "data", "config", "images", "sounds", "fonts", "shaders")


class PackagerError(Exception):
    pass


class Cancelled(PackagerError):
    pass


@dataclass
class Config:
    project: Path
    output: Path
    name: str
    java_home: Optional[Path] = None
    version: str = "1.0.0"
    build: str = "auto"  # auto, jar, maven, gradle, source
    kind: str = "gui"  # gui or cli
    fmt: str = "portable"  # portable (app-image) or installer (exe)
    jar: Optional[Path] = None
    main_class: str = ""
    icon: Optional[Path] = None
    runtime_image: Optional[Path] = None
    javafx_sdk: Optional[Path] = None  # JavaFX SDK lib folder
    javafx_modules: str = DEFAULT_MODULES
    extra_jars: list[Path] = field(default_factory=list)
    extra_resources: list[Path] = field(default_factory=list)
    java_options: list[str] = field(default_factory=list)


def validate_config(cfg: Config) -> None:
    if os.name != "nt":
        raise PackagerError("Chỉ tạo .exe Windows khi chạy công cụ trên Windows. jpackage không cross-compile.")
    if not cfg.project.is_dir():
        raise PackagerError(f"Thư mục dự án không tồn tại: {cfg.project}")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]{0,59}", cfg.name) or cfg.name.endswith((" ", ".")):
        raise PackagerError("Tên ứng dụng phải bắt đầu bằng chữ/số, dài tối đa 60 ký tự, chỉ có chữ/số/dấu cách/._-.")
    if not re.fullmatch(r"\d+(?:\.\d+){0,3}", cfg.version):
        raise PackagerError("Version cần dạng 1.0.0 (chỉ số và dấu chấm).")
    if cfg.main_class and not re.fullmatch(r"[\w$]+(?:\.[\w$]+)*", cfg.main_class):
        raise PackagerError("Main class phải có dạng com.example.Main hoặc Main.")
    for label, f in (("JAR", cfg.jar), ("Icon", cfg.icon)):
        if f is not None and not f.is_file():
            raise PackagerError(f"{label} không tồn tại: {f}")
    for f in cfg.extra_jars:
        if not f.is_file() or f.suffix.lower() != ".jar":
            raise PackagerError(f"JAR phụ trợ không hợp lệ: {f}")
    for f in cfg.extra_resources:
        if not f.exists():
            raise PackagerError(f"Tài nguyên không tồn tại: {f}")
    if cfg.icon and cfg.icon.suffix.lower() not in (".ico", ".png", ".jpg", ".jpeg"):
        raise PackagerError("Icon phải là .ico, .png hoặc .jpg/.jpeg")
    if cfg.java_home and not all((cfg.java_home / "bin" / (tool + ".exe")).is_file() for tool in ("java", "javac", "jpackage")):
        raise PackagerError("JAVA_HOME phải trỏ đến JDK Windows có bin/java.exe, bin/javac.exe và bin/jpackage.exe.")
    if cfg.runtime_image and not (cfg.runtime_image / "bin" / "java.exe").is_file():
        raise PackagerError("Runtime image phải có bin/java.exe (tạo bằng jlink trên Windows).")
    if cfg.javafx_sdk and not cfg.javafx_sdk.is_dir():
        raise PackagerError("Thư mục JavaFX SDK không tồn tại.")
    if cfg.build not in ("auto", "jar", "maven", "gradle", "source"):
        raise PackagerError("Build mode không hợp lệ.")
    if cfg.kind not in ("cli", "gui") or cfg.fmt not in ("portable", "installer"):
        raise PackagerError("Loại ứng dụng hoặc đầu ra không hợp lệ.")


def find_tool(name: str, java_home: Optional[Path] = None) -> str:
    exe = name + ".exe"
    jdk = str(java_home) if java_home else os.environ.get("JAVA_HOME", "").strip().strip('"')
    if jdk:
        path = Path(jdk) / "bin" / exe
        if path.is_file():
            return str(path)
    found = shutil.which(exe) or shutil.which(name)
    if not found:
        raise PackagerError(f"Không tìm thấy {exe}. Cần cài JDK 17+ và cấu hình JAVA_HOME/PATH.")
    return found


def log_command(cmd: list[str]) -> str:
    return subprocess.list2cmdline([str(x) for x in cmd])


class Runner:
    def __init__(self, callback: Callable[[str], None], cancel: Optional[threading.Event] = None, java_home: Optional[Path] = None):
        self.callback = callback
        self.cancel = cancel or threading.Event()
        self.proc: Optional[subprocess.Popen] = None
        self.java_home = java_home

    def say(self, msg: str) -> None:
        self.callback(msg)

    def run(self, cmd: list[str], cwd: Optional[Path] = None, allow_fail: bool = False) -> bool:
        if self.cancel.is_set():
            raise Cancelled("Đã hủy.")
        self.say("$ " + log_command(cmd))
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            self.proc = subprocess.Popen(
                [str(x) for x in cmd], cwd=str(cwd) if cwd else None,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, encoding="utf-8", errors="replace", bufsize=1,
                creationflags=flags, env=self._environment(),
            )
        except OSError as ex:
            raise PackagerError(f"Không thể chạy công cụ: {ex}") from ex
        proc = self.proc
        assert proc.stdout is not None
        output: queue.Queue = queue.Queue()
        sentinel = object()

        def stream_output() -> None:
            try:
                for line in proc.stdout:
                    output.put(line.rstrip("\r\n"))
            finally:
                output.put(sentinel)

        reader = threading.Thread(target=stream_output, daemon=True)
        reader.start()
        try:
            while True:
                if self.cancel.is_set():
                    self.stop()
                    raise Cancelled("Đã hủy.")
                try:
                    item = output.get(timeout=0.2)
                except queue.Empty:
                    continue
                if item is sentinel:
                    break
                self.say(item)
            ret = proc.wait()
        finally:
            if proc.poll() is not None:
                proc.stdout.close()
            self.proc = None
        if ret:
            if allow_fail:
                self.say(f"[CẢNH BÁO] Lệnh trả về mã {ret}.")
                return False
            raise PackagerError(f"Lệnh thất bại (exit code {ret}): {log_command(cmd)}")
        return True

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        if self.java_home:
            env["JAVA_HOME"] = str(self.java_home)
            env["PATH"] = str(self.java_home / "bin") + os.pathsep + env.get("PATH", "")
        return env

    def stop(self) -> None:
        self.cancel.set()
        if self.proc and self.proc.poll() is None:
            try:
                if os.name == "nt":
                    subprocess.run(["taskkill", "/F", "/T", "/PID", str(self.proc.pid)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                else:
                    self.proc.terminate()
            except Exception:
                self.proc.terminate()


def discover_build(project: Path, requested: str, manual_jar: Optional[Path]) -> str:
    if requested != "auto":
        return requested
    if manual_jar:
        return "jar"
    if (project / "pom.xml").is_file():
        return "maven"
    if (project / "build.gradle").is_file() or (project / "build.gradle.kts").is_file():
        return "gradle"
    if list(project.rglob("*.java")):
        return "source"
    if list(project.rglob("*.jar")):
        return "jar"
    raise PackagerError("Không tìm thấy pom.xml, build.gradle, *.java hoặc *.jar.")


def manifest_main(jar: Path) -> Optional[str]:
    try:
        with zipfile.ZipFile(jar) as z:
            with z.open("META-INF/MANIFEST.MF") as f:
                data = f.read().decode("utf-8", errors="replace")
            # MANIFEST.MF supports continued lines that begin with one space.
            data = re.sub(r"\r?\n ", "", data)
            match = re.search(r"(?im)^Main-Class:\s*(\S+)", data)
            return match.group(1) if match else None
    except (KeyError, zipfile.BadZipFile, OSError):
        return None


def choose_jar(candidates: list[Path], explicit: Optional[Path] = None) -> Path:
    if explicit:
        return explicit
    unique = sorted({p.resolve() for p in candidates if p.is_file() and p.suffix.lower() == ".jar"
                     and not p.name.lower().endswith(EXCLUDED_JAR_MARKERS)})
    if not unique:
        raise PackagerError("Không tìm thấy JAR đầu ra. Cần build dự án hoặc chỉ rõ đường dẫn JAR.")
    if len(unique) == 1:
        return unique[0]
    # Prefer a single assembled/fat JAR if clearly present.
    fat = [p for p in unique if re.search(r"(?:-all|-fat|-shaded)(?:-[\w.]+)?\.jar$", p.name, re.I)]
    if len(fat) == 1:
        return fat[0]
    runnable = [p for p in unique if manifest_main(p)]
    if len(runnable) == 1:
        return runnable[0]
    raise PackagerError("Có nhiều JAR, không xác định được JAR chính:\n" +
                        "\n".join(f" - {p}" for p in unique[:15]) +
                        "\nHãy chỉ rõ JAR chính tại ô 'JAR chính'.")


def find_main_class(project: Path) -> Optional[str]:
    found = set()
    for src in project.rglob("*.java"):
        # Never walk build/dependency cache dirs as source candidates.
        if any(part in ("build", "target", ".gradle", ".git", "node_modules") for part in src.parts):
            continue
        try:
            source = src.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if JAVA_SOURCE_RE.search(source):
            name = src.stem
            pkg = PACKAGE_RE.search(source)
            found.add((pkg.group(1) + "." if pkg else "") + name)
    return next(iter(found)) if len(found) == 1 else None


def find_netbeans_main(project: Path) -> Optional[str]:
    """Read the launcher class saved by NetBeans, when available."""
    props = project / "nbproject" / "project.properties"
    if not props.is_file():
        return None
    try:
        content = props.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*main\.class\s*=\s*([^\s#]+)", content)
    return match.group(1) if match and re.fullmatch(r"[\w$]+(?:\.[\w$]+)*", match.group(1)) else None


def prepare_icon(icon: Optional[Path], workspace: Path, runner: Runner) -> Optional[Path]:
    if icon is None:
        return None
    if icon.suffix.lower() == ".ico":
        return icon
    try:
        from PIL import Image, ImageOps
    except ImportError as ex:
        raise PackagerError("Để đổi PNG/JPG sang ICO, cài Pillow: py -m pip install Pillow") from ex
    target = workspace / "application.ico"
    with Image.open(icon) as original:
        original = ImageOps.exif_transpose(original).convert("RGBA")
        size = max(original.size)
        square = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        square.alpha_composite(original, ((size - original.width) // 2, (size - original.height) // 2))
        square = square.resize((256, 256), Image.Resampling.LANCZOS)
        square.save(target, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    runner.say(f"[ICON] Đã chuyển {icon.name} -> {target.name} (16..256px).")
    return target


def copy_jar_to_stage(src: Path, dest: Path) -> Path:
    target = dest / src.name
    if target.exists():
        if src.resolve() == target.resolve() or src.read_bytes() == target.read_bytes():
            return target
        raise PackagerError(f"Trùng tên JAR khác nội dung: {src.name}. Hãy đổi tên một file JAR.")
    shutil.copy2(src, target)
    return target


def stage_resources(project: Path, dest: Path, extras: list[Path], runner: Runner) -> None:
    for folder in EXTRA_RESOURCES:
        source = project / folder
        if source.is_dir():
            shutil.copytree(source, dest / folder, dirs_exist_ok=True)
            runner.say(f"[TÀI NGUYÊN] Đã thêm {folder}/")
    for source in extras:
        if source.is_dir():
            target = dest / source.name
            if target.exists():
                raise PackagerError(f"Tài nguyên bổ sung trùng thư mục: {target.name}")
            shutil.copytree(source, target)
        else:
            target = dest / source.name
            if target.exists():
                raise PackagerError(f"Tài nguyên bổ sung trùng file: {target.name}")
            shutil.copy2(source, target)
        runner.say(f"[TÀI NGUYÊN] Đã thêm {source.name}")


def source_files(project: Path) -> list[Path]:
    roots = [p for p in (project / "src" / "main" / "java", project / "src") if p.is_dir()]
    root = roots[0] if roots else project
    sources = [p for p in root.rglob("*.java")
               if not any(x in ("build", "target", ".git", ".gradle") for x in p.relative_to(root).parts)]
    return sorted(sources)


def make_jar_from_source(cfg: Config, temp: Path, stage: Path, runner: Runner) -> tuple[Path, list[Path]]:
    files = source_files(cfg.project)
    if not files:
        raise PackagerError("Không tìm thấy mã nguồn .java để compile.")
    if any(p.name == "module-info.java" for p in files):
        raise PackagerError("Chế độ source chỉ hỗ trợ dự án non-modular (không module-info.java)."
                            " Dùng Maven/Gradle hoặc xuất JAR phù hợp rồi đóng gói.")
    main = cfg.main_class or find_netbeans_main(cfg.project) or find_main_class(cfg.project)
    if not main:
        raise PackagerError("Không xác định được Main class. Nhập đầy đủ, ví dụ com.demo.Main.")
    classes = temp / "classes"
    classes.mkdir()
    libfiles = sorted((cfg.project / "lib").glob("*.jar")) + sorted((cfg.project / "libs").glob("*.jar"))
    libfiles += cfg.extra_jars
    libfiles = list(dict.fromkeys(f.resolve() for f in libfiles))
    javac = find_tool("javac", cfg.java_home)
    cmd = [javac, "-encoding", "UTF-8", "-d", str(classes)]
    classpath = list(libfiles)
    if cfg.javafx_sdk:
        # JavaFX SDK can be passed on module-path at compile time.
        fx_lib = normalize_fx_lib(cfg.javafx_sdk)
        cmd += ["--module-path", str(fx_lib), "--add-modules", cfg.javafx_modules or DEFAULT_MODULES]
    if classpath:
        cmd += ["-classpath", os.pathsep.join(str(x) for x in classpath)]
    # Argument file avoids Windows command length limit for large source trees.
    argfile = temp / "sources.txt"
    argfile.write_text("\n".join('"' + p.as_posix().replace('"', '\\"') + '"' for p in files), encoding="utf-8")
    cmd.append("@" + str(argfile))
    runner.run(cmd, cwd=cfg.project)
    # Copy classpath resources into JAR (FXML, CSS, textures loaded via getResource()).
    for resource_root in (cfg.project / "src" / "main" / "resources", cfg.project / "resources"):
        if resource_root.is_dir():
            shutil.copytree(resource_root, classes, dirs_exist_ok=True)
    # NetBeans/Ant projects commonly place assets, .properties and .txt inside src/.
    src_root = cfg.project / "src"
    if src_root.is_dir() and not (src_root / "main" / "java").is_dir():
        for resource in src_root.rglob("*"):
            if resource.is_file() and resource.suffix.lower() not in (".java", ".class"):
                target = classes / resource.relative_to(src_root)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(resource, target)
    jar = stage / f"{cfg.name}.jar"
    manifest = f"Manifest-Version: 1.0\r\nMain-Class: {main}\r\n\r\n"
    with zipfile.ZipFile(jar, "w", compression=zipfile.ZIP_DEFLATED) as zout:
        zout.writestr("META-INF/MANIFEST.MF", manifest)
        for file in classes.rglob("*"):
            if file.is_file():
                zout.write(file, file.relative_to(classes).as_posix())
    runner.say(f"[BUILD] Đã compile {len(files)} file Java -> {jar.name}")
    return jar, libfiles


def normalize_fx_lib(path: Path) -> Path:
    lib = path / "lib" if (path / "lib").is_dir() else path
    if not any(lib.glob("javafx*.jar")):
        raise PackagerError("Thư mục JavaFX SDK phải chứa các javafx*.jar (thường ở .../javafx-sdk-XX/lib).")
    return lib


def build_project(cfg: Config, workspace: Path, stage: Path, runner: Runner) -> Path:
    mode = discover_build(cfg.project, cfg.build, cfg.jar)
    runner.say(f"[CHẾ ĐỘ BUILD] {mode}")
    if mode == "source":
        mainjar, deps = make_jar_from_source(cfg, workspace, stage, runner)
        for dep in deps:
            copy_jar_to_stage(dep, stage)
        return mainjar

    deps: list[Path] = []
    if mode == "maven":
        if not (cfg.project / "pom.xml").is_file():
            raise PackagerError("Không có pom.xml trong thư mục dự án.")
        mvnw = cfg.project / "mvnw.cmd"
        mvn = str(mvnw) if mvnw.is_file() else (shutil.which("mvn.cmd") or shutil.which("mvn"))
        if not mvn:
            raise PackagerError("Thiếu Maven. Cài Maven hoặc giữ mvnw.cmd trong project.")
        runner.run([mvn, "-B", "-DskipTests", "package"], cwd=cfg.project)
        if not cfg.jar:
            # Prefer current module output, not historical/other submodules.
            candidates = list((cfg.project / "target").glob("*.jar"))
            chosen = choose_jar(candidates)
        else:
            chosen = cfg.jar
        dep_out = workspace / "maven_deps"
        # For fat/uber/shadow jar, additional Maven dependencies may be redundant.
        if not re.search(r"(?:-all|-fat|-shaded)(?:-[\w.]+)?\.jar$", chosen.name, re.I):
            runner.run([mvn, "-B", "dependency:copy-dependencies", "-DincludeScope=runtime",
                        f"-DoutputDirectory={dep_out}"], cwd=cfg.project)
            deps += list(dep_out.glob("*.jar"))
    elif mode == "gradle":
        if not ((cfg.project / "build.gradle").is_file() or (cfg.project / "build.gradle.kts").is_file()):
            raise PackagerError("Không có build.gradle hoặc build.gradle.kts.")
        wrapper = cfg.project / "gradlew.bat"
        gradle = str(wrapper) if wrapper.is_file() else (shutil.which("gradle.bat") or shutil.which("gradle"))
        if not gradle:
            raise PackagerError("Thiếu Gradle. Cài Gradle hoặc giữ gradlew.bat trong project.")
        runner.run([gradle, "build", "-x", "test"], cwd=cfg.project)
        if cfg.jar:
            chosen = cfg.jar
        else:
            chosen = choose_jar(list((cfg.project / "build" / "libs").glob("*.jar")))
        if not re.search(r"(?:-all|-fat|-shaded)(?:-[\w.]+)?\.jar$", chosen.name, re.I):
            # Requires Gradle's application plugin; failure means user may need to add dependency JARs.
            runner.run([gradle, "installDist"], cwd=cfg.project, allow_fail=True)
            install = cfg.project / "build" / "install"
            if install.is_dir():
                for libdir in install.glob("*/lib"):
                    deps += [p for p in libdir.glob("*.jar") if p.resolve() != chosen.resolve()]
            if not deps and not cfg.extra_jars:
                runner.say("[CẢNH BÁO] Không phát hiện JAR dependencies từ installDist."
                           " Nếu project có dependencies, nên dùng Gradle application/shadow plugin.")
    else:  # jar
        if cfg.jar:
            chosen = cfg.jar
        else:
            jars = list(cfg.project.glob("*.jar"))
            jars += list((cfg.project / "target").glob("*.jar"))
            jars += list((cfg.project / "build" / "libs").glob("*.jar"))
            chosen = choose_jar(jars)

    mainjar = copy_jar_to_stage(chosen, stage)
    # Include all local libraries, including next to manually supplied JAR.
    for libfolder in (cfg.project / "lib", cfg.project / "libs", cfg.project / "dependency", chosen.parent / "lib", chosen.parent / "libs"):
        if libfolder.is_dir():
            deps += list(libfolder.glob("*.jar"))
    deps += cfg.extra_jars
    for dep in dict.fromkeys(p.resolve() for p in deps):
        if dep == chosen.resolve():
            continue
        copy_jar_to_stage(dep, stage)
    return mainjar


def package(cfg: Config, callback: Callable[[str], None] = print,
            cancel: Optional[threading.Event] = None) -> Path:
    cfg = Config(**{**vars(cfg), "project": cfg.project.resolve(), "output": cfg.output.resolve()})
    validate_config(cfg)
    runner = Runner(callback, cancel, cfg.java_home)
    jpackage = find_tool("jpackage", cfg.java_home)
    find_tool("java", cfg.java_home)
    cfg.output.mkdir(parents=True, exist_ok=True)
    runner.say("[BẮT ĐẦU] Đóng gói trên Windows bằng JDK jpackage.")
    runner.say(f"[ĐẦU RA] {cfg.output}")
    with tempfile.TemporaryDirectory(prefix="java-exe-pack-") as directory:
        workspace = Path(directory)
        stage = workspace / "stage"
        stage.mkdir()
        mainjar = build_project(cfg, workspace, stage, runner)
        if cancel and cancel.is_set():
            raise Cancelled("Đã hủy.")
        icon = prepare_icon(cfg.icon, workspace, runner)
        stage_resources(cfg.project, stage, cfg.extra_resources, runner)

        effective_main = cfg.main_class or manifest_main(mainjar) or find_netbeans_main(cfg.project) or find_main_class(cfg.project)
        if not effective_main:
            raise PackagerError("JAR không có Main-Class trong manifest và không xác định được từ source."
                                " Hãy điền Main class (vd com.example.Main) vào giao diện.")
        runner.say(f"[JAR CHÍNH] {mainjar.name}")
        runner.say(f"[MAIN CLASS] {effective_main}")

        cmd = [jpackage, "--type", "exe" if cfg.fmt == "installer" else "app-image",
               "--dest", str(cfg.output), "--input", str(stage), "--name", cfg.name,
               "--app-version", cfg.version, "--main-jar", mainjar.name, "--main-class", effective_main]
        if cfg.kind == "cli":
            cmd.append("--win-console")
        if icon:
            cmd += ["--icon", str(icon)]
        if cfg.runtime_image:
            cmd += ["--runtime-image", str(cfg.runtime_image.resolve())]
        if cfg.fmt == "installer":
            cmd += ["--win-shortcut", "--win-menu", "--win-dir-chooser"]
        if cfg.javafx_sdk:
            fx_lib = normalize_fx_lib(cfg.javafx_sdk.resolve())
            target = stage / "javafx"
            shutil.copytree(fx_lib, target, dirs_exist_ok=True)
            runner.say(f"[JAVAFX] Đã thêm {len(list(target.iterdir()))} thư viện/file từ SDK.")
            # The Windows launcher expands APPDIR to its packaged app directory.
            # Keep module path in the packaged image rather than the build computer.
            if cfg.javafx_modules.strip():
                cmd += ["--java-options", f"--module-path=$APPDIR/javafx --add-modules={cfg.javafx_modules.strip()}"]
        for option in cfg.java_options:
            if option.strip():
                cmd += ["--java-options", option.strip()]
        runner.say("[JPACKAGE] Chuẩn bị Java runtime; bước này có thể cần dung lượng lớn.")
        runner.run(cmd)
        if cfg.fmt == "portable":
            output = cfg.output / cfg.name
            exe = output / f"{cfg.name}.exe"
            if not exe.is_file():
                raise PackagerError(f"jpackage báo thành công nhưng không tìm thấy launcher tại {exe}.")
            runner.say(f"[THÀNH CÔNG] Chạy: {exe}")
            runner.say("[LƯU Ý] Khi chuyển máy, sao chép CẢ thư mục ứng dụng (gồm runtime).")
        else:
            installs = sorted(cfg.output.glob(f"{cfg.name}-*.exe"), key=lambda x: x.stat().st_mtime, reverse=True)
            if not installs:
                raise PackagerError("jpackage báo thành công nhưng không tìm thấy file bộ cài EXE.")
            output = installs[0]
            runner.say(f"[THÀNH CÔNG] File cài đặt: {output}")
    return output


# ------------------------ Batch scanning and packaging ------------------------

SCAN_SKIP = {
    ".git", ".gradle", ".idea", ".vscode", "node_modules", "__pycache__",
    "build", "target", "dist", "nbproject", "out", ".settings", "venv", ".venv",
}


@dataclass(frozen=True)
class ProjectInfo:
    path: Path
    relative: str
    build: str
    main_class: str
    kind: str


@dataclass
class BatchResult:
    rows: list[dict]
    output: Path

    @property
    def succeeded(self) -> int:
        return sum(row["status"] == "success" for row in self.rows)

    @property
    def failed(self) -> int:
        return sum(row["status"] == "failed" for row in self.rows)

    @property
    def skipped(self) -> int:
        return sum(row["status"] == "skipped" for row in self.rows)


def is_project_root(folder: Path) -> bool:
    """Detect build roots, not arbitrary subfolders or documentation folders."""
    if any((folder / fn).is_file() for fn in ("pom.xml", "build.gradle", "build.gradle.kts", "build.xml")):
        return True
    for src in (folder / "src" / "main" / "java", folder / "src"):
        if src.is_dir() and next(src.rglob("*.java"), None) is not None:
            return True
    if any(folder.glob("*.java")):
        return True
    if any(folder.glob("*.jar")):
        return True
    return False


def guess_kind(folder: Path) -> str:
    """Best-effort UI detection. For unknown code prefer a visible console."""
    for src in source_files(folder):
        try:
            content = src.read_text(encoding="utf-8-sig", errors="replace")
        except OSError:
            continue
        if re.search(r"(?m)^\s*import\s+(?:javafx\.|javax\.swing\.|java\.awt\.|org\.lwjgl\.|com\.badlogic\.gdx\.|com\.jme3\.)", content):
            return "gui"
    return "cli"


def scan_projects(root: Path, exclude: Optional[Path] = None) -> list[ProjectInfo]:
    """Recursively find independent projects, but never descend into a found project."""
    if not root.is_dir():
        raise PackagerError(f"Không tồn tại thư mục: {root}")
    root = root.resolve()
    excluded = exclude.resolve() if exclude else None
    result: list[ProjectInfo] = []
    for current, directories, filenames in os.walk(root):
        path = Path(current)
        if excluded and (path == excluded or excluded in path.parents):
            directories[:] = []
            continue
        directories[:] = sorted(
            d for d in directories
            if d not in SCAN_SKIP and not d.startswith(".")
            and (not excluded or (path / d).resolve() != excluded)
        )
        if is_project_root(path):
            try:
                build = discover_build(path, "auto", None)
            except PackagerError:
                build = "source"
            main = find_netbeans_main(path) or find_main_class(path) or ""
            # A direct JAR root may have no source files.
            if not main and build == "jar":
                jars = list(path.glob("*.jar"))
                if len(jars) == 1:
                    main = manifest_main(jars[0]) or ""
            result.append(ProjectInfo(path, path.relative_to(root).as_posix() or ".", build, main, guess_kind(path)))
            directories[:] = []
    return result


@contextmanager
def open_batch_source(source: Path):
    """Accept a parent folder or safely unpack ZIP into a temporary directory."""
    source = source.expanduser().resolve()
    if source.is_dir():
        yield source
        return
    if not source.is_file() or source.suffix.lower() != ".zip":
        raise PackagerError("Nguồn hàng loạt phải là thư mục cha hoặc file ZIP.")
    with tempfile.TemporaryDirectory(prefix="java-batch-zip-") as temporary:
        dest = Path(temporary).resolve()
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > 200_000 or sum(i.file_size for i in infos) > 5 * 1024 ** 3:
                raise PackagerError("ZIP quá lớn để giải nén an toàn (giới hạn 200.000 file / 5 GiB).")
            for info in infos:
                name = info.filename.replace("\\", "/")
                parts = PurePosixPath(name).parts
                if not parts or name.startswith("/") or ".." in parts or ":" in parts[0]:
                    raise PackagerError(f"ZIP chứa đường dẫn không an toàn: {info.filename}")
                # Unix symlinks are deliberately not extracted.
                if (info.external_attr >> 16) & 0o170000 == 0o120000:
                    raise PackagerError(f"ZIP chứa symbolic link không hỗ trợ: {info.filename}")
                destpath = (dest / Path(*parts)).resolve()
                if not destpath.is_relative_to(dest):
                    raise PackagerError(f"ZIP chứa đường dẫn ngoài vùng giải nén: {info.filename}")
                if info.is_dir():
                    destpath.mkdir(parents=True, exist_ok=True)
                else:
                    destpath.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as inp, destpath.open("wb") as out:
                        shutil.copyfileobj(inp, out)
        yield dest


def inspect_batch(source: Path) -> list[ProjectInfo]:
    """For previews only: ProjectInfo paths within ZIP are temporary."""
    with open_batch_source(source) as root:
        return scan_projects(root)


def unique_app_names(projects: list[ProjectInfo]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    occupied: set[str] = set()
    for item in projects:
        basic = re.sub(r"[^A-Za-z0-9 ._-]", "_", item.path.name).strip(" ._") or "JavaApp"
        basic = basic[:60]
        candidate = basic
        if candidate.lower() in occupied:
            parentname = re.sub(r"[^A-Za-z0-9_-]", "_", PurePosixPath(item.relative).parent.name)
            candidate = (parentname + "_" + basic)[:60].strip(" ._")
        counter = 2
        original = candidate
        while candidate.lower() in occupied:
            candidate = (original[:55] + f"_{counter}")[:60]
            counter += 1
        occupied.add(candidate.lower())
        mapping[item.relative] = candidate
    return mapping


def write_batch_reports(output: Path, source: Path, rows: list[dict]) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    jsonfile = output / "batch_report.json"
    csvfile = output / "batch_report.csv"
    report = {
        "source": str(source), "total": len(rows),
        "succeeded": sum(r["status"] == "success" for r in rows),
        "failed": sum(r["status"] == "failed" for r in rows),
        "skipped": sum(r["status"] == "skipped" for r in rows),
        "projects": rows,
    }
    jsonfile.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    columns = ["project", "name", "build", "kind", "main_class", "status", "artifact", "error"]
    with csvfile.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return jsonfile, csvfile


def package_batch(source: Path, template: Config, *, only: Optional[list[str]] = None,
                  kind: str = "auto", skip_existing: bool = True,
                  callback: Callable[[str], None] = print,
                  cancel: Optional[threading.Event] = None) -> BatchResult:
    """Package each discovered project, continuing after individual errors."""
    if kind not in ("auto", "cli", "gui"):
        raise PackagerError("Loại ứng dụng hàng loạt phải là auto, cli hoặc gui.")
    source = source.expanduser().resolve()
    output = template.output.expanduser().resolve()
    if not source.exists():
        raise PackagerError(f"Không thấy nguồn: {source}")
    if source.is_dir() and (output == source or source in output.parents):
        # scan_projects excludes the output, but repeated runs could still be ambiguous
        callback("[LƯU Ý] Thư mục xuất nằm trong nguồn. Sẽ bỏ qua nó khi quét dự án.")
    with open_batch_source(source) as root:
        found = scan_projects(root, exclude=output)
        if only is not None:
            selection = {x.replace("\\", "/").strip("/").casefold() for x in only}
            found = [p for p in found if p.relative.casefold() in selection or p.path.name.casefold() in selection]
        if not found:
            raise PackagerError("Không phát hiện dự án Java nào phù hợp hoặc không có dự án được chọn.")
        names = unique_app_names(found)
        callback(f"[QUÉT] Có {len(found)} dự án để đóng gói.")
        rows: list[dict] = []
        for i, item in enumerate(found, 1):
            if cancel and cancel.is_set():
                callback("[HỦY] Đã dừng trước dự án tiếp theo.")
                break
            name = names[item.relative]
            targetkind = item.kind if kind == "auto" else kind
            row = {
                "project": item.relative, "name": name, "build": item.build,
                "kind": targetkind, "main_class": item.main_class,
                "status": "failed", "artifact": "", "error": "",
            }
            callback(f"\n{'=' * 64}\n[{i}/{len(found)}] {item.relative} => {name} ({targetkind})\n{'=' * 64}")
            cfg = replace(template, project=item.path, output=output, name=name,
                          kind=targetkind, main_class=item.main_class, jar=None)
            try:
                expected = output / name / f"{name}.exe" if cfg.fmt == "portable" else output / f"{name}-{cfg.version}.exe"
                if skip_existing and expected.is_file():
                    callback(f"[BỎ QUA] Đã tồn tại: {expected}")
                    row.update(status="skipped", artifact=str(expected))
                else:
                    built = package(cfg, callback=callback, cancel=cancel)
                    row.update(status="success", artifact=str(built))
            except Cancelled as ex:
                row.update(status="failed", error=str(ex))
                rows.append(row)
                callback("[HỦY] " + str(ex))
                break
            except (OSError, ValueError, PackagerError, subprocess.SubprocessError) as ex:
                row["error"] = str(ex)
                callback(f"[LỖI] {item.relative}: {ex}\n[TIẾP TỤC] Chuyển sang dự án kế tiếp.")
            rows.append(row)
        jsonfile, csvfile = write_batch_reports(output, source, rows)
        callback(f"\n[TỔNG KẾT] Thành công: {sum(r['status']=='success' for r in rows)} | "
                 f"Bỏ qua: {sum(r['status']=='skipped' for r in rows)} | "
                 f"Lỗi: {sum(r['status']=='failed' for r in rows)}")
        callback(f"[BÁO CÁO] {jsonfile}\n[BÁO CÁO] {csvfile}")
        return BatchResult(rows=rows, output=output)


# ----------------------------- Tkinter interface -----------------------------

def start_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("Java EXE Packager v2 | Đóng gói 1 dự án / hàng loạt / ZIP")
    root.geometry("1070x900")
    root.minsize(810, 660)
    try:
        ttk.Style().theme_use("clam")
    except tk.TclError:
        pass
    outer = ttk.Frame(root, padding=13)
    outer.pack(fill="both", expand=True)
    ttk.Label(outer, text="JAVA → WINDOWS EXE PACKAGER", font=("Segoe UI", 17, "bold")).pack(anchor="w")
    ttk.Label(outer, text="Java CLI • JavaFX / Swing / Game • 1 dự án hoặc nhiều folder/ZIP • PNG/JPG → ICO").pack(anchor="w", pady=(0, 9))
    common = {
        "output": tk.StringVar(value=str(Path.home() / "Desktop" / "JavaEXE")),
        "version": tk.StringVar(value="1.0.0"),
        "java_home": tk.StringVar(), "icon": tk.StringVar(),
        "runtime_image": tk.StringVar(), "javafx_sdk": tk.StringVar(),
        "javafx_modules": tk.StringVar(value=DEFAULT_MODULES),
        "extra_jars": tk.StringVar(), "extra_resources": tk.StringVar(),
        "java_options": tk.StringVar(), "fmt": tk.StringVar(value="portable"),
    }
    single = {
        "project": tk.StringVar(), "name": tk.StringVar(value="MyJavaApp"),
        "jar": tk.StringVar(), "main_class": tk.StringVar(),
        "build": tk.StringVar(value="auto"), "kind": tk.StringVar(value="gui"),
    }
    batch = {
        "source": tk.StringVar(), "kind": tk.StringVar(value="auto"),
        "skip_existing": tk.BooleanVar(value=True),
    }

    def make_entry(parent, row: int, label: str, variable, picker: str = ""):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=3)
        ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=8, pady=3)
        if picker:
            def browse():
                if picker == "dir":
                    p = filedialog.askdirectory(title=label)
                elif picker == "zip":
                    p = filedialog.askopenfilename(title=label, filetypes=[("ZIP", "*.zip")])
                elif picker == "jar":
                    p = filedialog.askopenfilename(title=label, filetypes=[("JAR", "*.jar")])
                elif picker == "image":
                    p = filedialog.askopenfilename(title=label, filetypes=[("ICO, PNG, JPG", "*.ico *.png *.jpg *.jpeg")])
                else:
                    p = ""
                if p:
                    variable.set(p)
                    if variable is single["project"]:
                        single["name"].set(Path(p).name[:60])
            ttk.Button(parent, text="Chọn...", command=browse, width=10).grid(row=row, column=2, pady=3)

    form = ttk.LabelFrame(outer, text="Cấu hình chung cho tất cả EXE", padding=8)
    form.pack(fill="x")
    form.columnconfigure(1, weight=1)
    make_entry(form, 0, "Thư mục xuất *", common["output"], "dir")
    make_entry(form, 1, "JAVA_HOME (JDK, tùy chọn)", common["java_home"], "dir")
    make_entry(form, 2, "Icon dùng chung (PNG/JPG/ICO)", common["icon"], "image")
    make_entry(form, 3, "Java Runtime image (tùy chọn)", common["runtime_image"], "dir")
    make_entry(form, 4, "JavaFX SDK (tùy chọn)", common["javafx_sdk"], "dir")
    make_entry(form, 5, "JavaFX modules (nếu dùng)", common["javafx_modules"])
    make_entry(form, 6, "Thêm JAR (; phân cách)", common["extra_jars"])
    make_entry(form, 7, "Thêm tài nguyên (; phân cách)", common["extra_resources"])
    make_entry(form, 8, "JVM options (; phân cách)", common["java_options"])
    options = ttk.Frame(form)
    options.grid(row=9, column=0, columnspan=3, sticky="w", pady=(5, 2))
    ttk.Label(options, text="Version").pack(side="left")
    ttk.Entry(options, textvariable=common["version"], width=12).pack(side="left", padx=(7, 24))
    ttk.Label(options, text="Kiểu xuất").pack(side="left")
    ttk.Combobox(options, textvariable=common["fmt"], values=("portable", "installer"),
                 state="readonly", width=18).pack(side="left", padx=7)
    ttk.Label(options, text="portable: thư mục .exe + runtime | installer: bộ cài .exe (cần WiX)").pack(side="left", padx=10)

    tabs = ttk.Notebook(outer)
    tabs.pack(fill="both", expand=True, pady=(9, 0))
    tab_single = ttk.Frame(tabs, padding=8)
    tab_batch = ttk.Frame(tabs, padding=8)
    tabs.add(tab_single, text="  1 DỰ ÁN  ")
    tabs.add(tab_batch, text="  HÀNG LOẠT: FOLDER / ZIP  ")

    oneform = ttk.Frame(tab_single)
    oneform.pack(fill="x")
    oneform.columnconfigure(1, weight=1)
    make_entry(oneform, 0, "Thư mục Java *", single["project"], "dir")
    make_entry(oneform, 1, "Tên EXE *", single["name"])
    make_entry(oneform, 2, "JAR chính (tùy chọn)", single["jar"], "jar")
    make_entry(oneform, 3, "Main Class (tùy chọn)", single["main_class"])
    singleopts = ttk.Frame(oneform)
    singleopts.grid(row=4, column=0, columnspan=3, sticky="w", pady=7)
    ttk.Label(singleopts, text="Build").pack(side="left")
    ttk.Combobox(singleopts, textvariable=single["build"], state="readonly",
                 values=("auto", "jar", "maven", "gradle", "source"), width=15).pack(side="left", padx=(8, 25))
    ttk.Label(singleopts, text="Ứng dụng").pack(side="left")
    ttk.Combobox(singleopts, textvariable=single["kind"], state="readonly",
                 values=("cli", "gui"), width=14).pack(side="left", padx=8)

    parentform = ttk.Frame(tab_batch)
    parentform.pack(fill="x")
    parentform.columnconfigure(1, weight=1)
    ttk.Label(parentform, text="Nguồn: thư mục cha hoặc .ZIP *").grid(row=0, column=0, sticky="w", pady=5)
    ttk.Entry(parentform, textvariable=batch["source"]).grid(row=0, column=1, sticky="ew", padx=8)
    ttk.Button(parentform, text="Chọn folder", command=lambda: batch["source"].set(
        filedialog.askdirectory(title="Chọn folder chứa nhiều dự án Java") or batch["source"].get())).grid(row=0, column=2, padx=2)
    ttk.Button(parentform, text="Chọn ZIP", command=lambda: batch["source"].set(
        filedialog.askopenfilename(filetypes=[("ZIP files", "*.zip")]) or batch["source"].get())).grid(row=0, column=3, padx=2)
    settings = ttk.Frame(tab_batch)
    settings.pack(fill="x", pady=(7, 7))
    ttk.Label(settings, text="Kiểu chạy").pack(side="left")
    ttk.Combobox(settings, textvariable=batch["kind"], state="readonly", width=12,
                 values=("auto", "cli", "gui")).pack(side="left", padx=(8, 25))
    ttk.Checkbutton(settings, text="Bỏ qua EXE đã có (tiếp tục lần chạy cũ)",
                    variable=batch["skip_existing"]).pack(side="left")
    hint = "auto = nhận diện GUI qua import JavaFX/Swing/AWT, còn lại dùng CLI. Có thể chọn nhiều dòng với Ctrl/Shift."
    ttk.Label(tab_batch, text=hint).pack(anchor="w")
    columns = ("path", "build", "main", "kind")
    tree = ttk.Treeview(tab_batch, columns=columns, show="headings", selectmode="extended", height=9)
    tree.heading("path", text="Thư mục dự án")
    tree.heading("build", text="Build")
    tree.heading("main", text="Main class")
    tree.heading("kind", text="Loại dự đoán")
    tree.column("path", width=270)
    tree.column("build", width=90, anchor="center")
    tree.column("main", width=245)
    tree.column("kind", width=120, anchor="center")
    tree.pack(fill="both", expand=True, pady=(6, 0))
    tree.bind("<Control-a>", lambda _event: (tree.selection_set(tree.get_children()), "break")[1])
    batchinfo = ttk.Label(tab_batch, text="Nhấn Quét dự án để xem danh sách. Nếu chưa quét, đóng gói sẽ chọn tất cả.")
    batchinfo.pack(anchor="w", pady=4)
    scanned: dict = {"source": None}

    bar = ttk.Frame(outer)
    bar.pack(fill="x", pady=(9, 4))
    status = tk.StringVar(value="Sẵn sàng")
    q: queue.Queue = queue.Queue()
    active: dict = {"thread": None, "event": None}
    logarea = scrolledtext.ScrolledText(outer, height=10, font=("Consolas", 10), wrap="word", state="disabled")

    def log(msg: str) -> None:
        logarea.configure(state="normal")
        logarea.insert("end", msg + "\n")
        logarea.see("end")
        logarea.configure(state="disabled")

    def base_config() -> Config:
        v = {key: var.get().strip() for key, var in common.items() if key != "fmt"}
        def opt(key: str) -> Optional[Path]:
            return Path(v[key]) if v[key] else None
        return Config(
            project=Path("."), output=Path(v["output"]), name="JavaApp",
            version=v["version"], java_home=opt("java_home"),
            icon=opt("icon"), runtime_image=opt("runtime_image"),
            javafx_sdk=opt("javafx_sdk"), javafx_modules=v["javafx_modules"],
            extra_jars=[Path(x.strip()) for x in v["extra_jars"].split(";") if x.strip()],
            extra_resources=[Path(x.strip()) for x in v["extra_resources"].split(";") if x.strip()],
            java_options=[x.strip() for x in v["java_options"].split(";") if x.strip()],
            fmt=common["fmt"].get(),
        )

    def set_busy(busy: bool, label: str = "Sẵn sàng") -> None:
        status.set(label)
        for btn in (btn_single, btn_batch, btn_scan):
            btn.configure(state="disabled" if busy else "normal")
        btn_cancel.configure(state="normal" if busy else "disabled")

    def run_worker(worker, label: str) -> None:
        if active["thread"] is not None:
            return
        event = threading.Event()
        active["event"] = event
        set_busy(True, label)
        def worker_wrapper():
            try:
                worker(event)
            except Exception as ex:
                q.put(("fail", str(ex)))
        active["thread"] = threading.Thread(target=worker_wrapper, daemon=True)
        active["thread"].start()

    def start_single() -> None:
        try:
            cfg = replace(base_config(), project=Path(single["project"].get().strip()),
                          name=single["name"].get().strip(),
                          jar=Path(single["jar"].get().strip()) if single["jar"].get().strip() else None,
                          main_class=single["main_class"].get().strip(),
                          build=single["build"].get(), kind=single["kind"].get())
            if not cfg.project.is_dir() or not cfg.name:
                raise PackagerError("Chọn thư mục dự án hợp lệ và nhập tên ứng dụng.")
        except Exception as ex:
            messagebox.showerror("Dữ liệu đầu vào", str(ex))
            return
        log("\n" + "=" * 55 + f"\nĐÓNG GÓI ĐƠN: {cfg.name}\n" + "=" * 55)
        def worker(event):
            result = package(cfg, lambda msg: q.put(("log", msg)), event)
            q.put(("single_done", str(result)))
        run_worker(worker, "Đang đóng gói 1 dự án...")

    def start_scan() -> None:
        source = Path(batch["source"].get().strip())
        if not source.exists():
            messagebox.showerror("Thiếu nguồn", "Chọn thư mục cha hoặc file .zip hợp lệ.")
            return
        scanned["source"] = None
        batchinfo.configure(text="Đang quét...")
        def worker(_event):
            found = inspect_batch(source)
            rows = [(p.relative, p.build, p.main_class or "(chưa tìm thấy)", p.kind) for p in found]
            q.put(("scan_done", (str(source.resolve()), rows)))
        run_worker(worker, "Đang quét danh sách dự án...")

    def start_batch() -> None:
        source = Path(batch["source"].get().strip())
        if not source.exists():
            messagebox.showerror("Thiếu nguồn", "Chọn thư mục cha hoặc file ZIP hợp lệ.")
            return
        base = base_config()
        selection = None
        if scanned["source"] == str(source.resolve()):
            selection = [tree.item(row, "values")[0] for row in tree.selection()]
            if not selection:
                messagebox.showwarning("Chưa chọn", "Chọn ít nhất một dự án trong danh sách, hoặc đổi nguồn để quét lại.")
                return
        log("\n" + "=" * 55 + "\nĐÓNG GÓI HÀNG LOẠT: " + str(source) + "\n" + "=" * 55)
        def worker(event):
            result = package_batch(source, base, only=selection,
                                   kind=batch["kind"].get(),
                                   skip_existing=batch["skip_existing"].get(),
                                   callback=lambda msg: q.put(("log", msg)), cancel=event)
            q.put(("batch_done", result))
        run_worker(worker, "Đang đóng gói hàng loạt...")

    btn_single = ttk.Button(bar, text="▶ ĐÓNG GÓI 1 DỰ ÁN", command=start_single)
    btn_single.pack(side="left")
    btn_scan = ttk.Button(bar, text="⌕ QUÉT DỰ ÁN", command=start_scan)
    btn_scan.pack(side="left", padx=5)
    btn_batch = ttk.Button(bar, text="▶ ĐÓNG GÓI HÀNG LOẠT", command=start_batch)
    btn_batch.pack(side="left")
    def stop():
        if active["event"]:
            active["event"].set()
            status.set("Đang yêu cầu hủy...")
            btn_cancel.configure(state="disabled")
    btn_cancel = ttk.Button(bar, text="■ HỦY", command=stop, state="disabled")
    btn_cancel.pack(side="left", padx=5)
    ttk.Button(bar, text="Mở đầu ra", command=lambda: os.startfile(str(Path(common["output"].get()).expanduser()))
               if Path(common["output"].get()).is_dir()
               else messagebox.showinfo("Thông báo", "Thư mục đầu ra chưa tồn tại.")).pack(side="left")
    ttk.Label(bar, textvariable=status).pack(side="right")
    ttk.Label(outer, text="LOG THỰC THI", font=("Segoe UI", 10, "bold")).pack(anchor="w", pady=(7, 0))
    logarea.pack(fill="both", expand=True, pady=(3, 0))

    def pump() -> None:
        try:
            while True:
                kind, data = q.get_nowait()
                if kind == "log":
                    log(data)
                    continue
                active["thread"] = None
                active["event"] = None
                set_busy(False)
                if kind == "fail":
                    log("✗ LỖI: " + data)
                    status.set("Đã gặp lỗi")
                    messagebox.showerror("Không hoàn thành", data)
                elif kind == "scan_done":
                    source, rows = data
                    for child in tree.get_children():
                        tree.delete(child)
                    for row in rows:
                        tree.insert("", "end", values=row)
                    tree.selection_set(tree.get_children())
                    scanned["source"] = source
                    batchinfo.configure(text=f"Phát hiện {len(rows)} dự án. Mặc định chọn tất cả; Ctrl/Shift để thay đổi.")
                    log(f"[QUÉT] Có {len(rows)} dự án. Đã chọn tất cả.")
                    status.set(f"Đã phát hiện {len(rows)} dự án")
                elif kind == "single_done":
                    log("✓ HOÀN THÀNH: " + data)
                    status.set("Thành công")
                    messagebox.showinfo("Đóng gói xong", "Đã tạo:\n" + data)
                elif kind == "batch_done":
                    summary = f"Thành công: {data.succeeded} | Bỏ qua: {data.skipped} | Lỗi: {data.failed}"
                    log("[HOÀN TẤT] " + summary)
                    status.set(summary)
                    if data.failed:
                        messagebox.showwarning("Xong hàng loạt (có lỗi)",
                                               summary + "\nXem batch_report.csv ở:\n" + str(data.output))
                    else:
                        messagebox.showinfo("Đã đóng gói hàng loạt", summary + "\n" + str(data.output))
        except queue.Empty:
            pass
        root.after(100, pump)
    root.after(100, pump)
    root.mainloop()


def main() -> int:
    if len(sys.argv) == 1:
        start_gui()
        return 0
    ap = argparse.ArgumentParser(description="Java → Windows EXE: một dự án hoặc nhiều dự án trong folder/ZIP")
    ap.add_argument("--project", type=Path, help="Thư mục một dự án Java")
    ap.add_argument("--batch-source", type=Path, help="Thư mục cha hoặc ZIP chứa nhiều dự án Java")
    ap.add_argument("--list-projects", action="store_true", help="Chỉ quét và liệt kê dự án, không tạo EXE")
    ap.add_argument("--only", action="append", help="Trong batch, chỉ chọn tên folder hoặc relative path; lặp lại nếu cần")
    ap.add_argument("--no-skip-existing", action="store_true", help="Trong batch, không bỏ qua EXE đã tồn tại")
    ap.add_argument("--output", type=Path, default=Path("dist-exe"))
    ap.add_argument("--name", help="Tên EXE khi đóng gói một dự án")
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--java-home", type=Path, help="Thư mục JDK Windows (nếu không đặt JAVA_HOME)")
    ap.add_argument("--build", choices=("auto", "jar", "maven", "gradle", "source"), default="auto")
    ap.add_argument("--kind", choices=("auto", "gui", "cli"), default="auto")
    ap.add_argument("--format", dest="fmt", choices=("portable", "installer"), default="portable")
    ap.add_argument("--jar", type=Path)
    ap.add_argument("--main-class", default="")
    ap.add_argument("--icon", type=Path)
    ap.add_argument("--runtime-image", type=Path)
    ap.add_argument("--javafx-sdk", type=Path)
    ap.add_argument("--javafx-modules", default=DEFAULT_MODULES)
    ap.add_argument("--add-jar", action="append", default=[], type=Path)
    ap.add_argument("--add-resource", action="append", default=[], type=Path)
    ap.add_argument("--java-option", action="append", default=[])
    args = ap.parse_args()
    if bool(args.project) == bool(args.batch_source):
        ap.error("Cần chọn một trong --project hoặc --batch-source")
    if args.project and not args.list_projects and not args.name:
        ap.error("Chế độ một dự án cần --name")
    if args.batch_source and (args.jar or args.main_class):
        ap.error("Trong batch, JAR/Main-Class được tự nhận diện theo từng dự án; không dùng --jar hoặc --main-class chung")
    try:
        config = Config(
            project=args.project or Path("."), output=args.output, name=args.name or "JavaApp",
            version=args.version, java_home=args.java_home, build=args.build,
            kind=args.kind if args.kind != "auto" else "cli", fmt=args.fmt,
            jar=args.jar, main_class=args.main_class, icon=args.icon,
            runtime_image=args.runtime_image, javafx_sdk=args.javafx_sdk,
            javafx_modules=args.javafx_modules, extra_jars=args.add_jar,
            extra_resources=args.add_resource, java_options=args.java_option,
        )
        if args.list_projects:
            source = args.batch_source or args.project
            found = inspect_batch(source)
            print(f"Phát hiện {len(found)} dự án Java:")
            for item in found:
                print(f"  {item.relative:45} build={item.build:7} kind={item.kind:3} main={item.main_class or '(chưa rõ)'}")
            return 0
        if args.batch_source:
            result = package_batch(args.batch_source, config, only=args.only,
                                   kind=args.kind, skip_existing=not args.no_skip_existing)
            print(f"KẾT QUẢ: thành công={result.succeeded}, bỏ qua={result.skipped}, lỗi={result.failed}")
            return 2 if result.failed else 0
        result = package(config)
        print("KẾT QUẢ:", result)
        return 0
    except (PackagerError, OSError, ValueError, zipfile.BadZipFile) as ex:
        print("LỖI:", ex, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
