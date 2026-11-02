import cv2
import base64
from PIL import Image
from io import BytesIO
from collections import deque
from app.mvp_pipeline import process_frames
import asyncio


def frame_to_b64(frame) -> str:
    """Convert an OpenCV frame (BGR) to Base64 JPEG."""
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    image = Image.fromarray(rgb)
    buffer = BytesIO()

    image.save(buffer, format="JPEG", quality=85)

    return base64.b64encode(buffer.getvalue()).decode("utf-8")


def create_temporal_grid(frames) -> str:
    """
    Combine 4 frames into a 2x2 grid.
    Muse Spark will analyze activity across time.
    """
    images = []

    for frame in frames:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)

        image.thumbnail((512, 512))
        images.append(image)

    grid = Image.new("RGB", (1024, 1024), "black")

    positions = [
        (0, 0),
        (512, 0),
        (0, 512),
        (512, 512)
    ]

    for image, position in zip(images, positions):
        grid.paste(image, position)

    buffer = BytesIO()
    grid.save(buffer, format="JPEG", quality=85)

    return base64.b64encode(buffer.getvalue()).decode("utf-8")

async def process_video(video_path: str):

    cap = cv2.VideoCapture(video_path)

    if not cap.isOpened():
        raise RuntimeError(f"Could not open video: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)

    duration = total_frames / fps if fps > 0 else 0

    print(f"Video duration: {duration:.2f} seconds")
    print(f"Video FPS: {fps}")

    # Sample one frame every 2 seconds
    sample_interval = 1.0

    # Keep the latest 4 sampled frames
    b64_frames = deque(maxlen=4)

    current_control_state ={
        "target_temperature_c": 30,
        "target_humidity_pct": 35,
        "target_co2_ppm": 900,
        "target_set_pm25_ug_m3": 15,
        "target_pm10_ug_m3": 25,
        "target_tvoc_ppb": 300,
    }

    manipulated_variables = {
        "temperature_c": 24.0,

        # Fan and airflow
        "fan_speed_mode": "AUTO",       # LOW, MED, HIGH, AUTO
        "fan_mode": "AUTO",             # ON, AUTO, OFF
        "airflow_rate_m3h": 500.0,      # Typical indoor airflow
        "louver_vertical_deg": 60,       # 0-90 degrees
        "louver_horizontal_deg": 90,     # 0-180 degrees
        "swing_mode": False,

        # Compressor
        "compressor_mode": "COOL",      # COOL, ECO, FAN_ONLY, OFF
        "compressor_frequency_hz": 40.0,
        "valve_open_pct": 50,

        # Energy and comfort
        "eco_mode": False,
        "sleep_mode": False,
        "turbo_mode": False,

        # Humidity and dehumidification
        "humidity_target_pct": 50.0,
        "dehumidification_mode": False,

        # Ventilation and air quality
        "fresh_air_damper_pct": 0,      # Closed for typical split AC
        "ventilation_mode": "AUTO",     # AUTO, ON, OFF

        # Maintenance and protection
        "filter_cleaning_alert": False,
        "defrost_mode": False
    }

    turn_history = deque(maxlen=3)

    frame_index = 0
    next_sample_time = 0.0
    last_response_id = None

    try:
        while True:

            success, frame = cap.read()

            if not success:
                break

            current_time = frame_index / fps

            # Sample a frame every 2 seconds
            if current_time >= next_sample_time:

                b64_frames.append(frame_to_b64(frame))

                next_sample_time += sample_interval

                # Wait until we have 4 frames for temporal analysis
                if len(b64_frames) == 4:

                    # Run your existing pipeline
                    result, telemetry, response_id = await process_frames(
                        b64_frames,
                        current_control_variables=current_control_state,
                        current_manipulated_variables=manipulated_variables,
                        turn_history=turn_history,
                    )

                    last_response_id = response_id
                    print(
                        f"\nTime: {current_time:.1f}s"
                    )
                    print("Last Reponse id", last_response_id)
                    print("Control update:", result)

                    # Update only variables that changed
                    manipulated_variables.update({
                        key: value
                        for key, value in result.items()
                        if value is not None
                    })

                    turn_history.append({
                        "telemetry": telemetry,
                        "decision_delta": result
                    })

            frame_index += 1

    finally:
        cap.release()

    print("\nVideo processing completed.")


if __name__ == "__main__":

    video_path = r"C:/Users/ch720/OneDrive/Desktop/SpotCool_AI/SpotCool-AI/videos/sample_1.mp4"

    asyncio.run(process_video(video_path))