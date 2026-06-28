import argparse
import sys
import urllib.request
from pathlib import Path

import cv2
import mediapipe as mp
from loguru import logger
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision

MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)
MODEL_CACHE = Path("models") / "hand_landmarker.task"
DOWNLOAD_ATTEMPTS = 3


def die(message):
    logger.error(message)
    sys.exit(1)


def get_model_path(override):
    if override:
        path = Path(override)
        if not path.is_file():
            die(f"model file not found: {path}")
        logger.info("using model {}", path)
        return path

    if not MODEL_CACHE.is_file():
        download_model(MODEL_CACHE)
    logger.info("using model {}", MODEL_CACHE)
    return MODEL_CACHE


def download_model(dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            logger.info("downloading model (attempt {}/{})", attempt, DOWNLOAD_ATTEMPTS)
            urllib.request.urlretrieve(MODEL_URL, dest)
            logger.success("model saved to {}", dest)
            return
        except OSError as exc:
            logger.warning(
                "download attempt {}/{} failed: {}", attempt, DOWNLOAD_ATTEMPTS, exc
            )

    die(
        f"could not download hand landmarker model from {MODEL_URL}\n"
        f"download it manually and pass --model PATH"
    )


def create_landmarker(model_path):
    base_options = mp_python.BaseOptions(model_asset_path=str(model_path))
    options = vision.HandLandmarkerOptions(base_options=base_options, num_hands=1)
    return vision.HandLandmarker.create_from_options(options)


def finger_up(tip, pip, landmarks):
    return landmarks[tip].y < landmarks[pip].y


def is_peace(landmarks):
    index_up = finger_up(8, 6, landmarks)
    middle_up = finger_up(12, 10, landmarks)

    ring_up = finger_up(16, 14, landmarks)
    pinky_up = finger_up(20, 18, landmarks)

    return index_up and middle_up and not ring_up and not pinky_up


def open_source(source):
    is_webcam = source.isdigit()
    cap = cv2.VideoCapture(int(source) if is_webcam else source)

    if is_webcam:
        # WSL2/usbipd cams stall (select() timeout) on the default uncompressed
        # format; MJPG is what the USB-over-IP bridge can actually stream.
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

    if not cap.isOpened():
        die(f"cannot open source: {source}")
    logger.info("opened source {}", source)
    return cap


def detect_peace(landmarker, rgb_frame):
    mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_frame)
    result = landmarker.detect(mp_image)

    return any(is_peace(landmarks) for landmarks in result.hand_landmarks)


def process_frame(frame, landmarker):
    frame = cv2.flip(frame, 1)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    if detect_peace(landmarker, rgb):
        frame = cv2.GaussianBlur(frame, (61, 61), 0)
    return frame


def run_to_file(cap, landmarker, output, is_image):
    frame = None
    success, raw_frame = cap.read()

    if not success:
        die("could not read a frame from source")
    frame = process_frame(raw_frame, landmarker)

    if is_image:
        cv2.imwrite(output, frame)
        logger.success("wrote {}", output)
        return

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    height, width = frame.shape[:2]
    writer = cv2.VideoWriter(output, fourcc, fps, (width, height))

    writer.write(frame)
    while True:
        success, raw_frame = cap.read()
        if not success:
            break
        writer.write(process_frame(raw_frame, landmarker))
    writer.release()
    logger.success("wrote {}", output)


def run_to_window(cap, landmarker):
    window = "Foto Aku Ngeblur"
    try:
        cv2.namedWindow(window, cv2.WINDOW_NORMAL)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        if width and height:
            cv2.resizeWindow(window, width, height)
    except cv2.error:
        die("no display available, use --output PATH to write to a file instead")

    logger.info("press ESC or close the window to quit")
    first = True
    while True:
        success, raw_frame = cap.read()
        if not success:
            if first:
                die(
                    "no frames from source (camera opened but delivered nothing); "
                    "on WSL2/usbipd check the cam streams MJPG"
                )
            break
        first = False

        cv2.imshow(window, process_frame(raw_frame, landmarker))

        # stop on ESC or when the window is closed via its X button
        if cv2.waitKey(1) & 0xFF == 27:
            break
        try:
            if cv2.getWindowProperty(window, cv2.WND_PROP_VISIBLE) < 1:
                break
        except cv2.error:
            break  # Qt destroys the window on close; the next query throws


def main():
    parser = argparse.ArgumentParser(
        description="Blur the frame when a peace sign is detected"
    )
    parser.add_argument(
        "--source",
        default="0",
        help="webcam index, or path to a video/image file (default: 0)",
    )
    parser.add_argument(
        "--model", help="path to hand_landmarker.task (default: auto-download)"
    )
    parser.add_argument(
        "--output", help="write result to this file instead of opening a window"
    )
    args = parser.parse_args()

    landmarker = create_landmarker(get_model_path(args.model))
    cap = open_source(args.source)

    try:
        if args.output:
            is_image = not args.source.isdigit() and Path(
                args.source
            ).suffix.lower() in {
                ".jpg",
                ".jpeg",
                ".png",
                ".bmp",
            }
            run_to_file(cap, landmarker, args.output, is_image)
        else:
            run_to_window(cap, landmarker)
    finally:
        cap.release()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
