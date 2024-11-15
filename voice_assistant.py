import streamlit as st
import sounddevice as sd
import numpy as np
import wave
import tempfile
import os
from pathlib import Path
from gtts import gTTS
import boto3
import json
from vosk import Model, KaldiRecognizer
import pytz
from datetime import datetime

# Initialize session state variables
if "messages" not in st.session_state:
    st.session_state.messages = []
if "model" not in st.session_state:
    st.session_state.model = None
if "recording_state" not in st.session_state:
    st.session_state.recording_state = "idle"
if "bedrock_client" not in st.session_state:
    st.session_state.bedrock_client = boto3.client(
        service_name="bedrock-runtime", region_name="us-east-1"
    )

# Tool definitions
TOOLS = [
    {
        "name": "get_time",
        "description": "Get the current time in a specific city",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "The name of the city"}
            },
            "required": ["city"],
        },
    },
    {
        "name": "get_weather",
        "description": "Get the current weather in a specific city",
        "input_schema": {
            "type": "object",
            "properties": {
                "city": {"type": "string", "description": "The name of the city"}
            },
            "required": ["city"],
        },
    },
]


def text_to_speech(text, output_path):
    """Convert text to speech using gTTS"""
    try:
        tts = gTTS(text=text, lang="en")
        tts.save(output_path)
    except Exception as e:
        st.error(f"Error in text-to-speech: {str(e)}")
        raise


def execute_tool(tool_name, tool_args):
    """Execute the requested tool"""
    if tool_name == "get_time":
        try:
            city = tool_args["city"]
            timezone_mapping = {
                "new york": "America/New_York",
                "london": "Europe/London",
                "tokyo": "Asia/Tokyo",
                "sydney": "Australia/Sydney",
                "paris": "Europe/Paris",
                "delhi": "Asia/Kolkata",
                "singapore": "Asia/Singapore",
            }
            tz_name = timezone_mapping.get(city.lower())
            if tz_name:
                timezone = pytz.timezone(tz_name)
                current_time = datetime.now(timezone)
                return (
                    f"The current time in {city} is {current_time.strftime('%I:%M %p')}"
                )
            return f"Sorry, I don't have timezone information for {city}"
        except Exception as e:
            return f"Error getting time: {str(e)}"

    elif tool_name == "get_weather":
        # Simulated weather data
        weather_data = {
            "new york": "72°F, Partly Cloudy",
            "london": "18°C, Rainy",
            "tokyo": "25°C, Sunny",
            "sydney": "22°C, Clear",
            "paris": "20°C, Cloudy",
            "delhi": "32°C, Sunny",
            "singapore": "30°C, Thunderstorms",
        }
        city = tool_args["city"].lower()
        return (
            f"Current weather in {city}: {weather_data.get(city, 'Data not available')}"
        )

    return "Tool execution failed"


def get_claude_response(query):
    """Get response from Claude 3 Sonnet via Bedrock with tool calling"""
    try:
        # Request to Claude
        body = json.dumps(
            {
                "anthropic_version": "bedrock-2023-05-31",
                "max_tokens": 150,
                "messages": [{"role": "user", "content": query}],
                "tools": TOOLS,
                "system": "You are a helpful assistant. Always provide very concise responses in 2-3 lines maximum. When asked about time or weather, use the appropriate tools to get real-time data.",
            }
        )

        print("\n=== Request Body ===")
        print(json.dumps(json.loads(body), indent=2))

        response = st.session_state.bedrock_client.invoke_model(
            body=body, modelId="anthropic.claude-3-sonnet-20240229-v1:0"
        )

        response_body = json.loads(response.get("body").read())
        print("\n=== Response Body ===")
        print(json.dumps(response_body, indent=2))

        # Check if Claude wants to use a tool
        if response_body.get("stop_reason") == "tool_use":
            tool_calls = [
                content
                for content in response_body.get("content", [])
                if content.get("type") == "tool_use"
            ]

            print("\n=== Tool Calls ===")
            print(json.dumps(tool_calls, indent=2))

            # Execute tool and return result directly
            if tool_calls:
                tool_call = tool_calls[0]  # Get first tool call
                tool_name = tool_call.get("name")
                tool_args = tool_call.get("input", {})
                result = execute_tool(tool_name, tool_args)
                return result

        # If no tool was used, extract the response text
        if isinstance(response_body.get("content"), list) and response_body["content"]:
            if isinstance(response_body["content"][0], dict):
                response_text = response_body["content"][0].get("text", "").strip()
            else:
                response_text = str(response_body["content"][0]).strip()
        else:
            response_text = str(response_body.get("content", "")).strip()

        return response_text

    except Exception as e:
        error_msg = f"Error calling Claude: {str(e)}"
        print(f"\n=== Error ===\n{error_msg}")
        return f"I apologize, but I encountered an error: {str(e)}"


# Initialize Vosk model
def initialize_vosk():
    if st.session_state.model is None:
        with st.spinner(
            "Downloading speech recognition model (this may take a minute)..."
        ):
            model = Model(lang="en-us")
            st.session_state.model = model
            st.success("Speech recognition model loaded!")


def record_audio(duration=5, sample_rate=16000):
    recording = sd.rec(
        int(duration * sample_rate), samplerate=sample_rate, channels=1, dtype=np.int16
    )
    sd.wait()
    return recording, sample_rate


def save_audio(recording, sample_rate):
    temp_dir = tempfile.mkdtemp()
    temp_path = Path(temp_dir) / "recording.wav"

    with wave.open(str(temp_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(recording.tobytes())

    return str(temp_path)


def speech_to_text(audio_path):
    try:
        wf = wave.open(audio_path, "rb")
        rec = KaldiRecognizer(st.session_state.model, wf.getframerate())

        result = ""
        while True:
            data = wf.readframes(4000)
            if len(data) == 0:
                break
            if rec.AcceptWaveform(data):
                result_dict = json.loads(rec.Result())
                result += result_dict.get("text", "") + " "

        final_result = json.loads(rec.FinalResult())
        result += final_result.get("text", "")

        return result.strip()
    except Exception as e:
        st.error(f"Error in speech recognition: {str(e)}")
        return ""


# Streamlit UI
st.title("Voice Assistant")

# Initialize Vosk model
initialize_vosk()

# Chat interface
for message in st.session_state.messages:
    with st.chat_message(message["role"]):
        st.write(message["content"])
        if "audio" in message:
            st.audio(message["audio"])

# Record button
if st.button(
    "🎤 Record (5 seconds)", disabled=st.session_state.recording_state != "idle"
):
    try:
        # Step 1: Record audio
        with st.spinner("Recording..."):
            recording, sample_rate = record_audio()
            audio_path = save_audio(recording, sample_rate)
            st.session_state.recording_state = "recorded"

        # Step 2: Convert to text
        with st.spinner("Converting speech to text..."):
            text = speech_to_text(audio_path)

            if text and text.strip():
                st.session_state.messages.append(
                    {"role": "user", "content": f"You: {text}"}
                )

                # Step 3: Get Claude response
                with st.spinner("Getting response from Claude..."):
                    response_text = get_claude_response(text)

                    if response_text:
                        try:
                            # Generate audio response
                            response_audio_path = os.path.join(
                                tempfile.mkdtemp(), "response.mp3"
                            )
                            text_to_speech(response_text, response_audio_path)

                            # Add response to chat
                            st.session_state.messages.append(
                                {
                                    "role": "assistant",
                                    "content": f"Assistant: {response_text}",
                                    "audio": response_audio_path,
                                }
                            )
                        except Exception as e:
                            st.error(f"Error in text-to-speech processing: {str(e)}")
                            st.session_state.messages.append(
                                {
                                    "role": "assistant",
                                    "content": f"Assistant: {response_text}",
                                }
                            )
                    else:
                        st.error("No response received from Claude")
            else:
                st.warning("No speech detected. Please try again.")

            # Clean up
            os.remove(audio_path)
            st.session_state.recording_state = "idle"
            st.rerun()

    except Exception as e:
        st.error(f"An error occurred: {str(e)}")
        st.session_state.recording_state = "idle"

# Clear chat button
if st.button("Clear Chat"):
    st.session_state.messages = []
    st.session_state.recording_state = "idle"
    st.rerun()

# Display microphone status
if st.checkbox("Check Microphone"):
    try:
        devices = sd.query_devices()
        st.write("Available audio devices:")
        for i, device in enumerate(devices):
            st.write(f"{i}: {device['name']}")
    except Exception as e:
        st.error(f"Error checking audio devices: {str(e)}")
