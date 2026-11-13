# GitHub Downloader (Tkinter)

Ứng dụng Python 3.10+ tải file, thư mục (gồm thư mục con) hoặc toàn bộ mã nguồn một repo GitHub. Không cần cài Git. Không tải lịch sử commit.

## Cài và chạy

Windows: mở CMD/PowerShell tại thư mục đã giải nén:

```console
py -m pip install -r requirements.txt
py github_downloader.py
```

Thonny: cài `requests` bằng Tools → Manage packages, mở `github_downloader.py`, bấm Run. Cần cài thư viện đúng trình thông dịch mà Thonny đang dùng.

Kali/Ubuntu/Debian:

```console
sudo apt install python3-tk python3-venv
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python github_downloader.py
```

## Sử dụng

1. Trên github.com, mở file hoặc folder cần tải rồi sao chép URL trên thanh địa chỉ.
2. Dán vào **Link GitHub**. Các dạng URL (OWNER, REPO, BRANCH, PATH là chỗ thay thế):
   - File: `https://github.com/OWNER/REPO/blob/BRANCH/PATH/file.py`
   - Folder: `https://github.com/OWNER/REPO/tree/BRANCH/PATH/folder`
   - Cả repo: `https://github.com/OWNER/REPO`
3. **Branch/tag** thường để trống. Nếu tên branch có `/` hoặc URL dễ nhầm, nhập chính xác tên branch/tag đang xuất hiện trong link, ví dụ `feature/gui`. Với link gốc repo, có thể dùng ô này để chọn branch/tag/commit thay cho branch mặc định.
4. Repo public: thường không cần token. Repo private hoặc API hết lượt: nhập GitHub token có quyền đọc nội dung repo (Contents: Read). Token không được lưu xuống đĩa hay ghi vào nhật ký.
5. Bấm **Chọn folder…**, chọn thư mục lưu trên máy.
6. Bấm **Tải xuống**. Xem tiến trình và nhật ký. Nút **Hủy** dừng sau khi yêu cầu mạng đang chờ kết thúc (read timeout 30 giây khi không nhận dữ liệu).

Khi tải **một file**, file được lưu trực tiếp vào thư mục bạn chọn, không tạo folder bọc ngoài. Ví dụ: `C:\Users\DucAnh\Downloads\demoPDF\Kali Linux Cookbook.pdf`. Nếu đã có file trùng tên, bản tải mới được thêm `(1)`, `(2)`… trước phần mở rộng; không ghi đè bản cũ.

Khi tải **folder hoặc cả repo**, vẫn tạo thư mục `github_download_<mã ngẫu nhiên>` như bản trước và giữ cây thư mục con. Chưa có tính năng resume.

Ví dụ minh họa cấu trúc: tải folder `src` sẽ lưu tại `NƠI_LƯU/github_download_<mã>/src/...`.

## Cơ chế và giới hạn

- Dùng GitHub REST API để chốt commit, duyệt Git tree từng cấp và tải Git blob. Kiểm tra kích thước và SHA-1 Git blob trước khi đổi file tạm thành file hoàn chỉnh.
- Tải trên luồng nền; Tkinter chỉ cập nhật trên luồng giao diện.
- Lỗi kết nối ban đầu/HTTP 5xx được thử lại tối đa 3 lần; lỗi giữa lúc tải một file được báo lỗi và chuyển file kế tiếp. HTTP 401/403/429 dừng tác vụ để tránh gọi tiếp khi token/quota không cho phép.
- Repo nhiều file sẽ tốn nhiều lượt API. HTTP 403 có thể là thiếu quyền hoặc hết quota. HTTP 404 có thể là sai link hoặc chưa có quyền đọc repo private.
- Không hỗ trợ GitHub Enterprise, link raw, release assets hoặc tải nhiều link trong một lần.
- Bỏ qua symbolic link và submodule, ghi rõ trong nhật ký. Không tạo file thực thi từ symbolic link.
- Git LFS: chỉ tải pointer được lưu trong Git, chưa tải nội dung LFS; nhật ký hiển thị cảnh báo. Git blob lớn hơn 100 MiB được báo lỗi.
- Tên file không tương thích Windows (ví dụ dấu `:`), đường dẫn không an toàn, hoặc danh sách GitHub bị cắt sẽ làm tác vụ dừng rõ ràng, không âm thầm báo tải đủ.
- Giữ nguyên bytes của file nhưng không khôi phục quyền executable, timestamps hoặc metadata Git. Không tự thực thi nội dung tải xuống.
- Với URL có nhiều cách phân chia ref/path hợp lệ, tự chọn ref hợp lệ dài nhất. Nhập branch/tag chính xác để loại bỏ sự mơ hồ.
- Không lưu folder Git rỗng; Git vốn không theo dõi thư mục rỗng.

Tài liệu API: https://docs.github.com/en/rest/git/trees và https://docs.github.com/en/rest/git/blobs

## Kiểm tra bản bàn giao

Đã kiểm tra cú pháp và các ca kiểm thử offline bằng API giả lập: phân tích URL, branch có dấu `/`, tải folder lồng nhau, so khớp nội dung, chống đường dẫn vượt thư mục, hủy và xử lý lỗi. Không coi các kiểm thử này là xác nhận tải mạng trực tiếp thành công trên máy người dùng.
