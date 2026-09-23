import cv2
import pytesseract
import soc_scraper as api
import soc_account

api.setup_tesseract()
hwnd, _ = api.find_window("Sword of Convallaria")
frame = api.capture_window(hwnd)
diagnostics = []
print("FIXED ROSTER DETECTION:", soc_account.bootstrap_page(frame, "roster", diagnostics))
print("Heading diagnostics:", diagnostics)
h, w = frame.shape[:2]
for label, image, psm in (
    ("original", frame[round(h * .04):round(h * .32)], 11),
    ("heading", frame[round(h * .04):round(h * .12), round(w * .15):round(w * .56)], 7),
    ("counter", frame[round(h * .045):round(h * .12), round(w * .78):round(w * .995)], 7),
):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.resize(gray, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    print(label, repr(pytesseract.image_to_string(gray, config=f"--psm {psm}", timeout=6)))
