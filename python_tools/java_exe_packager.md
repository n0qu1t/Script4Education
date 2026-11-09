# Java EXE Packager v2 — Hướng dẫn sử dụng (Windows, JDK 17)

**Java EXE Packager** là chương trình Python có **giao diện Tkinter** và **CLI**. Công cụ nhận một dự án Java hoặc nhiều dự án nằm trong **thư mục cha / file ZIP**, xây dựng ứng dụng và gọi `jpackage` trong JDK để tạo:

- **Portable (`app-image`)**: một **thư mục ứng dụng** chứa launcher `.exe`, `app/` và `runtime/`; sao chép **cả thư mục** khi chuyển sang máy khác.
- **Installer (`exe`)**: một **file bộ cài `.exe`** có thể chuyển sang máy Windows khác để chạy cài đặt. Sau khi cài đặt, launcher vẫn sử dụng các thư mục ứng dụng/runtime được cài trên máy đích.

Công cụ có thể xử lý Java CLI (console), Swing/AWT, JavaFX và ứng dụng/game Java **nếu các dependency, native libraries và tài nguyên của dự án được cung cấp đúng**. Không bảo đảm mọi dự án Java tùy biến đều đóng gói tự động thành công.

> **Quan trọng:** `portable` **không phải một file `.exe` độc lập duy nhất**. Việc chỉ chép launcher `.exe` mà không chép `app/`, `runtime/` có thể gây lỗi **“Failed to launch JVM”**. `installer` là file cài đặt chứ không phải launcher portable.

---

## 1. Các phần mềm phải cài (phân biệt Portable và Installer)

| Thành phần | Portable | Installer | Công dụng |
|---|:---:|:---:|---|
| **Windows 10/11** | Bắt buộc | Bắt buộc | Build `.exe` Windows phải thực hiện trên Windows. |
| **Python 3.10+ (có Tkinter)** | Bắt buộc | Bắt buộc | Chạy công cụ Python/giao diện. |
| **JDK 17 (đầy đủ)** | Bắt buộc | Bắt buộc | Cung cấp `java`, `javac`, `jpackage`, `jlink`; JDK 17 dùng được, không bắt buộc JDK 21. |
| **Pillow (Python)** | Khi dùng PNG/JPG | Khi dùng PNG/JPG | Chuyển `.png`, `.jpg`, `.jpeg` sang `.ico`. Nếu chỉ dùng `.ico` hoặc không chọn icon thì không cần. |
| **WiX Toolset 3.x** | **Không cần** | **Bắt buộc** | Cho `jpackage --type exe` của JDK 17; khuyên dùng WiX 3.14.1. |
| **Maven / Maven Wrapper** | Tùy dự án | Tùy dự án | Dùng khi dự án có `pom.xml`. |
| **Gradle / Gradle Wrapper** | Tùy dự án | Tùy dự án | Dùng khi dự án có `build.gradle` hoặc `build.gradle.kts`. |
| **JavaFX SDK cho Windows** | Tùy dự án | Tùy dự án | Cần khi dự án JavaFX chưa tự tích hợp đầy đủ JavaFX dependencies/native runtime. |
| **Java Runtime image tạo sẵn** | Tùy chọn | Tùy chọn | Nếu không điền, `jpackage` sẽ tự tạo Java runtime bằng `jlink`. |

**Máy chỉ để chạy ứng dụng đã đóng gói** thông thường **không cần Python, WiX hay cài riêng JDK 17**, vì runtime được đóng gói kèm; vẫn có thể cần DLL, redistributable hoặc thành phần hệ thống khác do ứng dụng sử dụng.

### 1.1. Cài Python và thư viện pip

Cài Python 3.10 trở lên từ [python.org](https://www.python.org/downloads/windows/) (nên bật tùy chọn thêm Python vào PATH). Sau đó mở **CMD/PowerShell** ở thư mục chứa `java_exe_packager.py`:

```powershell
py -m pip install --upgrade pip
py -m pip install -r requirements.txt
```

`requirements.txt` của phiên bản này chỉ chứa:

```text
Pillow>=10.0.0
```

Có thể cài trực tiếp:

```powershell
py -m pip install "Pillow>=10.0.0"
```

**Không cần pip install riêng** `tkinter`, `os`, `sys`, `pathlib`, `zipfile`, `csv`, `json`, `threading`, `subprocess`, `tempfile`... vì đây là thư viện đi cùng Python (Tkinter có trong hầu hết bộ cài Python Windows chính thức). **JDK, WiX, Maven, Gradle không cài bằng pip**.

Kiểm tra Python, Tkinter và Pillow:

```powershell
py --version
py -m tkinter
py -c "from PIL import Image; print('Pillow OK')"
```

Nếu `py -m tkinter` mở cửa sổ thử nghiệm thì Tkinter hoạt động. Nếu lệnh `py` không tồn tại, thử `python` thay cho `py`.

### 1.2. Cài và cấu hình JDK 17

Cài **JDK 17 đầy đủ**, không phải chỉ JRE. Thư mục JDK ví dụ:

```text
C:\Program Files\Java\jdk-17
```

Đường dẫn chính xác phụ thuộc bộ JDK/bản cập nhật đã cài; bạn cần tự xác định trên máy. Bên trong JDK thường có:

```text
<JDK_HOME>\
└── bin\
    ├── java.exe
    ├── javac.exe
    ├── jar.exe
    ├── jlink.exe
    └── jpackage.exe
```

**Cấu hình biến môi trường Windows** (khuyến nghị):

1. Nhấn `Win + R`, nhập `sysdm.cpl` → **Advanced** → **Environment Variables**.
2. Tạo biến `JAVA_HOME` trỏ vào **thư mục gốc JDK**, **không** trỏ vào `bin` hoặc `java.exe`.
3. Trong biến `Path`, thêm `%JAVA_HOME%\bin`.
4. Mở **CMD/PowerShell mới** và kiểm tra:

```cmd
echo %JAVA_HOME%
where java
where javac
where jlink
where jpackage
java -version
javac -version
jpackage --version
```

`jpackage --version` nên hiện `17` nếu bạn muốn build với JDK 17. Nếu có nhiều JDK, có thể điền **JAVA_HOME (JDK, tùy chọn)** ngay trong giao diện, hoặc dùng `--java-home` khi chạy CLI.

> **Portable cần JDK và `jpackage`, nhưng không cần WiX**. Không cần chọn ô **Java Runtime image** nếu muốn `jpackage` tự tạo runtime.

### 1.3. Cài WiX để tạo Installer (JDK 17)

**Chỉ cần bước này khi chọn `installer`.** Với JDK 17, dùng **WiX Toolset v3.14.1** để có hai công cụ quan trọng:

```text
candle.exe
light.exe
```

Tải từ trang dự án: [WiX Toolset 3.14.1 (GitHub Releases)](https://github.com/wixtoolset/wix3/releases/tag/wix3141rtm); chọn bộ cài **`wix314.exe`** (không nhầm với ZIP mã nguồn). WiX 4/5/6 dùng công cụ giao diện lệnh khác và **không thay thế trực tiếp WiX 3** cho bản `jpackage` của JDK 17.

Ví dụ thư mục thường gặp sau khi cài WiX 3.14:

```text
C:\Program Files (x86)\WiX Toolset v3.14\bin\
├── candle.exe
└── light.exe
```

Đường dẫn này chỉ là **ví dụ**. Nếu không thấy `bin`, hãy tìm `candle.exe` bằng công cụ tìm kiếm của Windows hoặc chạy PowerShell:

```powershell
Get-ChildItem 'C:\Program Files', 'C:\Program Files (x86)' -Filter candle.exe -Recurse -ErrorAction SilentlyContinue |
    Select-Object -ExpandProperty FullName
```

Thêm **thư mục chứa `candle.exe` và `light.exe`** vào biến môi trường **Path**, rồi mở **CMD mới** kiểm tra:

```cmd
where candle
where light
candle -?
light -?
```

Nếu `where candle`/`where light` không trả về đường dẫn: WiX chưa có trong PATH, chưa cài build tools, hoặc đã cài sai phiên bản. Nếu WiX dạng ZIP portable, giải nén đầy đủ và đưa **đúng thư mục chứa hai `.exe`** vào PATH; không bắt buộc thư mục phải có tên `bin`.

**WiX chỉ cần ở máy tạo Installer**, không cần cài trên máy nhận bộ cài.

### 1.4. Maven, Gradle và các file trong `bin` (nếu dự án sử dụng)

| Kiểu dự án | Phát hiện qua | Công cụ cần thêm |
|---|---|---|
| Java thuần | Các file `*.java`, thư mục `src/` | Không cần Maven/Gradle; dùng `javac` của JDK. |
| JAR có sẵn | File `*.jar` | Không cần Maven/Gradle nếu JAR và dependency đã đủ. |
| Maven | `pom.xml` | `mvnw.cmd` trong dự án **hoặc** `mvn.cmd` từ thư mục `bin` của Maven có trong PATH. |
| Gradle | `build.gradle`, `build.gradle.kts` | `gradlew.bat` trong dự án **hoặc** `gradle.bat` từ thư mục `bin` của Gradle có trong PATH. |
| JavaFX | Mã nguồn/JAR JavaFX | Có thể cần JavaFX SDK Windows đúng phiên bản và các JAR/DLL đi kèm. |

Ví dụ nếu cài Maven/Gradle toàn cục, các thư mục cần thêm vào **Path** thường là:

```text
<MAVEN_HOME>\bin
<GRADLE_HOME>\bin
```

Kiểm tra:

```cmd
where mvn
mvn -version
where gradle
gradle --version
```

Chỉ kiểm tra công cụ mà dự án thực sự sử dụng. Nếu dự án có Maven/Gradle **Wrapper**, không cần cài Maven/Gradle toàn cục, nhưng wrapper phải đầy đủ và có thể cần kết nối mạng để tải bản công cụ/dependency lần đầu.

---

## 2. Bảng yêu cầu nhanh: Portable khác Installer ở đâu?

| Nội dung | `portable` | `installer` |
|---|---|---|
| Tham số `--format` | `portable` | `installer` |
| Lệnh `jpackage` tương ứng | `--type app-image` | `--type exe` |
| Yêu cầu JDK 17 | Có | Có |
| Yêu cầu WiX 3 | **Không** | **Có** |
| Đầu ra | **Thư mục** chứa `.exe`, `app/`, `runtime/` | **Một file cài đặt `.exe`** |
| Chuyển sang máy khác | Chép **nguyên cả thư mục** | Chép **file Installer**, rồi chạy cài đặt |
| Máy đích cần tự cài Java không? | Thường không | Thường không |
| Tạo shortcut Start Menu/Desktop | Không tự cài shortcut | Bản này truyền `--win-menu`, `--win-shortcut`, `--win-dir-chooser` |

**Để kiểm thử hoặc chạy không cần cài đặt, nên tạo Portable trước.** Khi ứng dụng Portable chạy đúng, mới tạo Installer.

---

## 3. Khởi động công cụ

Giải nén gói source, mở CMD/PowerShell trong thư mục có file `java_exe_packager.py` và chạy:

```powershell
py -m pip install -r requirements.txt
py java_exe_packager.py
```

Hoặc nhấn đúp **`START_WINDOWS.bat`**. Đây là bộ khởi động giao diện chung.

Để xem danh sách tham số CLI:

```powershell
py java_exe_packager.py --help
```

> Công cụ phải chạy **trên Windows** khi tạo Windows `.exe`; không hỗ trợ tạo EXE Windows trực tiếp từ Linux/Kali hoặc macOS bằng cách chạy `jpackage` ở nền tảng đó.

---

## 4. Giải thích các trường trong giao diện

### 4.1. Cấu hình chung

| Trường | Bắt buộc? | Ý nghĩa |
|---|---|---|
| **Thư mục xuất** | Có | Thư mục lưu kết quả. Nên dùng đường dẫn riêng, ngoài source. |
| **JAVA_HOME (JDK)** | Không, **nếu** đã nhận JDK đúng qua `JAVA_HOME`/PATH | Thư mục gốc JDK 17, không gồm `bin`. |
| **Icon dùng chung (PNG/JPG/ICO)** | Không | PNG/JPG tự đổi thành `.ico` nhờ Pillow; để trống dùng icon mặc định. |
| **Java Runtime image** | Không | Thư mục runtime được tạo sẵn bằng `jlink`, có `bin/java.exe`; để trống cho `jpackage` tự tạo. |
| **JavaFX SDK** | Không, trừ khi dự án cần cấp SDK riêng | Thư mục `javafx-sdk-XX` hoặc `javafx-sdk-XX/lib` chứa `javafx*.jar`. |
| **JavaFX modules** | Chỉ khi dùng JavaFX SDK và cần khai báo modules | Mặc định `javafx.controls,javafx.fxml`; điều chỉnh theo dự án. |
| **Thêm JAR (`;` phân cách)** | Khi thiếu dependency | Đường dẫn các JAR phụ thuộc, ngăn cách bằng dấu `;`. |
| **Thêm tài nguyên (`;` phân cách)** | Khi thiếu tài nguyên | File/folder cần kèm ứng dụng, ví dụ ảnh, âm thanh, config, native DLL. |
| **JVM options (`;` phân cách)** | Không | Các tùy chọn JVM, ví dụ `-Xmx2g;-Dfile.encoding=UTF-8`. |
| **Version** | Có giá trị mặc định | Dạng số, ví dụ `1.0.0`. |
| **Kiểu xuất** | Có | `portable` hoặc `installer`. |

**Mục ghi “tùy chọn” có thể để trống khi tính năng tương ứng không cần.** Tuy nhiên, nếu ứng dụng bắt buộc có JavaFX SDK, thư viện phụ thuộc hay tài nguyên ngoài, bạn vẫn phải cung cấp đúng những thứ đó.

### 4.2. Tab **1 DỰ ÁN**

| Trường | Tác dụng |
|---|---|
| **Thư mục Java** | Folder gốc của một dự án có source, JAR hoặc file build. |
| **Tên EXE** | Tên chương trình sẽ tạo (bắt buộc ở chế độ một dự án). |
| **JAR chính** | Để trống nếu công cụ tự chọn được JAR. Điền khi có nhiều JAR hoặc muốn dùng JAR cụ thể. |
| **Main Class** | Để trống nếu tìm được từ JAR manifest, NetBeans `main.class`, hoặc source; nếu không, nhập tên đầy đủ như `com.example.Main`. |
| **Build** | `auto`, `jar`, `maven`, `gradle`, `source`. |
| **Ứng dụng** | `cli` hiện console để nhập/xuất; `gui` ẩn cửa sổ console. |

Nhấn **ĐÓNG GÓI 1 DỰ ÁN** và xem phần **LOG THỰC THI**.

### 4.3. Tab **HÀNG LOẠT: FOLDER / ZIP**

1. Chọn **thư mục cha** có nhiều dự án con hoặc chọn **file `.zip`** chứa các dự án.
2. Nhấn **QUÉT DỰ ÁN** để xem danh sách dự án được phát hiện.
3. Mặc định danh sách đã chọn tất cả. Dùng **Ctrl/Shift** để chọn một phần, hoặc **Ctrl+A** để chọn lại tất cả.
4. Chọn **Kiểu chạy**: `auto`, `cli` hoặc `gui`.
5. Chọn **Kiểu xuất** ở phần cấu hình chung: `portable` / `installer`.
6. Bật **Bỏ qua EXE đã có** nếu muốn chạy tiếp những dự án chưa có đầu ra.
7. Nhấn **ĐÓNG GÓI HÀNG LOẠT**.
8. Xem kết quả và báo cáo `batch_report.csv`, `batch_report.json` tại thư mục xuất.

Nếu chưa quét trước đó, công cụ sẽ tự quét và thực hiện với tất cả dự án phù hợp. Nếu đã quét, công cụ chỉ làm các dự án được chọn.

**Cách quét hiện tại:** phát hiện project từ `pom.xml`, `build.gradle`, `build.gradle.kts`, `build.xml`, `src/` có `.java`, `.java`/`.jar` ở thư mục gốc. Khi gặp một project root, công cụ **không tiếp tục đi sâu** để coi các thư mục con là dự án khác. Với dự án nhiều module, nên chọn từng module ứng dụng tương ứng.

**Phân loại `auto` là suy đoán:** công cụ tìm import JavaFX/Swing/AWT và một số game engine; nếu không thấy thì có thể chọn CLI. Hãy tự chọn `gui` hoặc `cli` nếu biết rõ loại chương trình.

**Khi lỗi:** batch tiếp tục với dự án kế tiếp, ghi `success`, `failed`, `skipped` và thông tin lỗi vào báo cáo. Tùy chọn bỏ qua dựa trên **sự tồn tại của file EXE**; không xác minh EXE đã chạy đúng và không tự sửa build cũ.

---

## 5. Ví dụ chung: đóng gói 1 dự án Java

Giả sử nguồn Java nằm trong thư mục `C:\Projects\MyApp`.

**Tạo Portable ứng dụng CLI:**

```powershell
py java_exe_packager.py --project "C:\Projects\MyApp" --name MyApp --build auto --kind cli --format portable --java-home "C:\Program Files\Java\jdk-17" --output "C:\Release"
```

**Tạo Installer ứng dụng GUI:**

```powershell
py java_exe_packager.py --project "C:\Projects\MyApp" --name MyApp --build auto --kind gui --format installer --java-home "C:\Program Files\Java\jdk-17" --icon "C:\Icons\app.png" --output "C:\Release"
```

Với JAR có sẵn, chỉ rõ file JAR và Main Class khi cần:

```powershell
py java_exe_packager.py --project "C:\Projects\MyApp" --name MyApp --build jar --jar "C:\Projects\MyApp\MyApp.jar" --main-class com.example.Main --kind gui --format portable
```

**Lưu ý:** tên thư mục và đường dẫn trên chỉ mang tính minh họa; hãy thay bằng đường dẫn thực tế trên máy của bạn. Phiên bản JDK phải phù hợp với Java bytecode của ứng dụng.

---

## 6. Ví dụ chung: đóng gói nhiều dự án Java

Giả sử thư mục `C:\Projects\JavaCollection` chứa các project con độc lập.

**Chỉ quét, chưa build:**

```powershell
py java_exe_packager.py --batch-source "C:\Projects\JavaCollection" --list-projects
```

**Đóng gói tất cả thành Portable:**

```powershell
py java_exe_packager.py --batch-source "C:\Projects\JavaCollection" --kind auto --format portable --output "C:\Release\Portable"
```

**Đóng gói tất cả thành Installer:**

```powershell
py java_exe_packager.py --batch-source "C:\Projects\JavaCollection" --kind auto --format installer --java-home "C:\Program Files\Java\jdk-17" --output "C:\Release\Installers"
```

**Nguồn là ZIP:**

```powershell
py java_exe_packager.py --batch-source "C:\Projects\JavaCollection.zip" --kind cli --format portable --output "C:\Release\Portable"
```

**Chọn riêng một số dự án** (`--only` có thể lặp lại):

```powershell
py java_exe_packager.py --batch-source "C:\Projects\JavaCollection" --only AppOne --only AppTwo --kind cli --format installer --output "C:\Release\Installers"
```

**Build lại** thay vì tự bỏ qua file đã tồn tại:

```powershell
py java_exe_packager.py --batch-source "C:\Projects\JavaCollection" --no-skip-existing --format portable --output "C:\Release\Portable"
```

> `--no-skip-existing` chỉ tắt chức năng **bỏ qua** của batch; **không bảo đảm ghi đè**. Nếu thư mục image hoặc file Installer trùng tên đã tồn tại, `jpackage` có thể báo lỗi. Hãy sao lưu rồi chủ động xóa/đổi tên **đầu ra cũ** trước khi build lại.

### Kết quả dự kiến

Portable:

```text
Release\Portable\
├── AppOne\
│   ├── AppOne.exe
│   ├── app\
│   └── runtime\
├── AppTwo\
│   ├── AppTwo.exe
│   ├── app\
│   └── runtime\
├── batch_report.csv
└── batch_report.json
```

Installer:

```text
Release\Installers\
├── AppOne-1.0.0.exe
├── AppTwo-1.0.0.exe
├── batch_report.csv
└── batch_report.json
```

Tên đầu ra có thể khác nếu tên dự án trùng nhau và công cụ phải tự đổi tên để tránh xung đột.

---

## 7. Cách công cụ build source và các phụ thuộc

| `--build` | Xử lý thực tế của source Python |
|---|---|
| `auto` | Ưu tiên JAR được chỉ định, sau đó Maven (`pom.xml`), Gradle (`build.gradle`), source Java, cuối cùng JAR có sẵn. |
| `source` | Dùng `javac` biên dịch `.java`, tạo JAR có Main-Class và copy các resource thường gặp. |
| `jar` | Chọn JAR có sẵn và gom các JAR cạnh dự án trong các thư mục `lib/`, `libs/`, `dependency/` nếu có. |
| `maven` | Chạy Maven `package -DskipTests`, sau đó cố gắng copy các runtime dependency qua `dependency:copy-dependencies`. |
| `gradle` | Chạy `gradle build -x test`; nếu cần, thử `installDist` để lấy runtime JARs. |

Source thường tự lấy tài nguyên từ `assets/`, `resources/`, `data/`, `config/`, `images/`, `sounds/`, `fonts/`, `shaders/` ở thư mục dự án; tài nguyên ở nơi khác cần thêm qua **Thêm tài nguyên** / `--add-resource`.

**Maven/Gradle không phải lúc nào cũng tự đóng gói đủ dependency.** Với game engine, JavaFX, JNI, DLL native hoặc custom plugin, bạn có thể phải sửa cấu hình build, tạo runnable/fat JAR hoặc cung cấp JAR và tài nguyên bổ sung. Nếu có nhiều lớp `main()` hoặc nhiều JAR chính, nên đóng gói từng dự án để chỉ rõ Main Class/JAR.

### JavaFX và game

- JavaFX thường yêu cầu các thư viện JavaFX phù hợp với **Windows** và phiên bản JDK/JavaFX mà dự án hỗ trợ.
- Trong giao diện, chọn **JavaFX SDK** nếu dự án chưa tự gom đủ JavaFX library; ô modules mặc định là `javafx.controls,javafx.fxml` khi SDK được cấp.
- Game có asset, âm thanh, texture, shader hoặc DLL phải có đầy đủ các file này và đường dẫn đọc tài nguyên phải hoạt động trên máy khác.
- Với ứng dụng JavaFX không modular, một lớp launcher Java thông thường (không trực tiếp `extends Application`) có thể cần thiết nếu gặp lỗi JavaFX runtime khi chạy JAR đóng gói.
- Nếu dự án đã có custom runtime tạo bằng `jlink`, có thể chọn **Java Runtime image**; không cần khi dùng runtime tự tạo của `jpackage`.

---

## 8. Tham số CLI đầy đủ của phiên bản này

| Tham số | Ý nghĩa |
|---|---|
| `--project PATH` | Đóng gói **một** dự án. |
| `--batch-source PATH` | Đóng gói **nhiều** dự án từ folder/ZIP. Không dùng cùng `--project`. |
| `--list-projects` | Quét và liệt kê dự án; không tạo EXE. |
| `--only NAME` | Chọn dự án trong batch; có thể lặp lại. |
| `--no-skip-existing` | Không tự bỏ qua file EXE đã tồn tại trong batch. |
| `--output PATH` | Thư mục xuất (mặc định `dist-exe`). |
| `--name NAME` | Tên ứng dụng, bắt buộc với chế độ một dự án. |
| `--version N.N.N` | Phiên bản ứng dụng (mặc định `1.0.0`). |
| `--java-home PATH` | Thư mục gốc JDK. |
| `--build auto\|jar\|maven\|gradle\|source` | Cách tìm/build JAR. |
| `--kind auto\|cli\|gui` | Console hoặc ứng dụng GUI; `auto` dùng nhận diện trong batch. |
| `--format portable\|installer` | Loại đầu ra. |
| `--jar PATH` | JAR chính (một dự án). |
| `--main-class CLASS` | Lớp Main (một dự án). |
| `--icon PATH` | `.ico`, `.png`, `.jpg`, `.jpeg`. |
| `--runtime-image PATH` | Java runtime image có sẵn. |
| `--javafx-sdk PATH` | Thư mục JavaFX SDK hoặc `lib`. |
| `--javafx-modules LIST` | Danh sách module JavaFX, ngăn cách bằng dấu phẩy. |
| `--add-jar PATH` | JAR phụ thêm; lặp lại nếu cần. |
| `--add-resource PATH` | File/thư mục tài nguyên; lặp lại nếu cần. |
| `--java-option OPTION` | JVM option; lặp lại nếu cần. |

Kiểm tra cú pháp chính xác bằng `py java_exe_packager.py --help`.

---

## 9. Sử dụng icon PNG/JPG hoặc không chọn icon

- Nếu **không chọn** icon, công cụ vẫn đóng gói và dùng biểu tượng mặc định của launcher.
- Nếu chọn `.ico`, công cụ truyền trực tiếp file này cho `jpackage`.
- Nếu chọn `.png`, `.jpg` hoặc `.jpeg`, công cụ dùng **Pillow** chuyển thành file `.ico` trong thư mục tạm; không sửa hình gốc.
- Ảnh nên vuông và độ phân giải ít nhất **256 × 256** để có icon rõ; chương trình có thể thêm phần trong suốt để giữ nguyên tỷ lệ ảnh.
- Icon **không ảnh hưởng** đến việc chọn Portable hay Installer.

---

## 10. Kiểm tra đầu ra và chuyển sang máy Windows khác

### 10.1. Portable

1. Mở thư mục đầu ra, tìm thư mục ứng dụng có launcher `.exe`, `app/`, `runtime/`.
2. Chạy launcher `.exe` trên máy build để kiểm tra.
3. **Chép hoặc nén ZIP toàn bộ thư mục ứng dụng** rồi chuyển sang máy khác.
4. Giải nén đầy đủ và chạy launcher trong thư mục vừa giải nén.
5. Nếu lỗi, kiểm tra thư mục `runtime/bin` và `app` còn nguyên vẹn.

Để kiểm tra runtime trong PowerShell (mở tại thư mục chứa launcher):

```powershell
Test-Path '.\runtime\bin\server\jvm.dll'
Get-ChildItem '.\app'
Get-ChildItem '.\runtime\bin'
```

Đây là phép kiểm tra sơ bộ; kết quả đầy đủ phụ thuộc cấu trúc runtime của gói. Không chỉ sao chép `MyApp.exe`.

### 10.2. Installer

1. Ở máy build, tạo bộ cài với `--format installer`.
2. **Chép file Installer `.exe` ở thư mục output**, không lấy file launcher `.exe` trong thư mục đã cài hoặc thư mục ứng dụng.
3. Chạy Installer trên máy Windows đích, làm theo trình cài đặt.
4. Mở ứng dụng qua shortcut hoặc trong thư mục được cài.
5. Nếu ứng dụng không chạy, kiểm tra runtime, bản build, dependency và chính sách bảo mật trên máy đích.

**Lưu ý bảo mật:** Windows SmartScreen hoặc phần mềm bảo mật có thể cảnh báo phần mềm chưa có chữ ký số. Đây không phải lỗi Python/WiX; nếu phân phối công khai, nên cân nhắc ký số code signing phù hợp.

---

## 11. Lỗi thường gặp và cách xử lý

| Lỗi/triệu chứng | Nguyên nhân có thể | Hướng xử lý |
|---|---|---|
| `'py' is not recognized` | Chưa có Python Launcher/Python PATH | Cài Python hoặc thử `python`. |
| `ModuleNotFoundError: PIL` | Chưa cài Pillow | `py -m pip install -r requirements.txt`. |
| `No module named tkinter` | Python không kèm Tkinter | Cài bản Python Windows chính thức có Tcl/Tk. |
| `jpackage is not recognized` | Không tìm được JDK `bin` | Kiểm tra `JAVA_HOME`, PATH và `where jpackage`. |
| `candle.exe` / `light.exe` không tìm thấy | WiX 3 chưa cài hoặc chưa vào PATH | Cài WiX 3.14.1, thêm thư mục có hai EXE vào PATH. Portable không cần WiX. |
| Installer build lỗi nhưng Portable được | WiX/installer metadata hoặc cấu hình bộ cài có vấn đề | Kiểm tra log `jpackage`, WiX 3 và quyền tạo file. |
| `Main class not found` | Không có/không xác định được điểm khởi chạy | Chỉ định `--main-class` hoặc sửa manifest/build. |
| `NoClassDefFoundError` | Thiếu JAR phụ thuộc | Tạo fat JAR hoặc thêm dependency đúng phiên bản. |
| `JavaFX runtime components are missing` | JavaFX dependency/module/launcher thiếu hoặc sai | Dùng SDK/runtime JavaFX đúng, xem lại Main Class, module configuration. |
| `UnsatisfiedLinkError` | Thiếu DLL/native library hoặc sai kiến trúc | Bổ sung native DLL và kiểm tra x64/x86/ARM. |
| Game mất âm thanh/ảnh | Thiếu asset hoặc đường dẫn hard-code | Dùng resource từ classpath hoặc bổ sung tài nguyên. |
| **`Failed to launch JVM` trên máy khác** | Thiếu `runtime/`, runtime bị hỏng, sai kiến trúc hoặc cấu hình JVM không hợp lệ | **Không copy riêng launcher EXE**; chuyển toàn bộ Portable hoặc chạy đúng Installer; kiểm tra runtime và log. |
| Tên EXE đã có, build lại bị lỗi | `jpackage` không ghi đè | Đổi output/tên/version hoặc sao lưu và xóa bản build cũ. |
| Có nhiều file JAR, không chọn được | Không biết JAR nào chứa entry point | Chọn **JAR chính** và **Main Class** rõ ràng. |

Khi xử lý lỗi, nên thử build **một dự án** trước khi build hàng loạt, và chạy `jar`/`java` trực tiếp để xác nhận dự án thực sự chạy được độc lập.

---

## 12. Kiểm thử công cụ

Chạy test Python (các bài test không thay thế việc tạo EXE thực tế trên Windows):

```powershell
py -m unittest -v test_batch.py test_packager.py
```

Với mỗi ứng dụng sau đóng gói nên kiểm thử ít nhất: khởi động, nhập/xuất console (nếu CLI), tải resource/assets, truy cập file cần thiết, âm thanh/đồ họa (nếu game), chạy trên **máy Windows sạch** cùng kiến trúc, và chạy trực tiếp từ shortcut (nếu Installer).

Công cụ có chạy Maven/Gradle/Java build script do dự án cung cấp. **Chỉ build source hoặc ZIP đáng tin cậy**.

---

## 13. Tài liệu tham khảo chính thức

- [Oracle JDK 17 — Packaging Overview](https://docs.oracle.com/en/java/javase/17/jpackage/packaging-overview.html): yêu cầu nền tảng, WiX và Java runtime kèm ứng dụng.
- [Oracle JDK 17 — `jpackage` command](https://docs.oracle.com/en/java/javase/17/docs/specs/man/jpackage.html): các tham số và loại output.
- [WiX Toolset 3.14.1 — GitHub release](https://github.com/wixtoolset/wix3/releases/tag/wix3141rtm): bộ cài `wix314.exe` và WiX binaries.
- [OpenJFX — Documentation](https://openjfx.io/openjfx-docs/): yêu cầu JavaFX và cách build.
- [Python — Tkinter](https://docs.python.org/3/library/tkinter.html) và [Pillow](https://pillow.readthedocs.io/): giao diện và xử lý icon.

> **Phạm vi tài liệu:** Nội dung đối chiếu theo `java_exe_packager.py` phiên bản v2 hiện có. Các câu lệnh thực hiện tạo EXE phải chạy và kiểm tra trên máy **Windows** có đủ JDK/WiX; tài liệu không khẳng định một dự án bất kỳ sẽ build được nếu thiếu dependency hoặc bị lỗi source.
