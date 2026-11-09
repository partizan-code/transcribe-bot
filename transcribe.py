from assemblyai import TranscriptStatus
from assemblyai.prerecorded.v2 import Transcriber, TranscriptionConfig
import config


audio_file = "example.mp3"

assembly_config = TranscriptionConfig(
    language_detection=True,
    speaker_labels=True,
)

transcriber = Transcriber(api_key=config.API_KEY)
transcript = transcriber.transcribe(audio_file, config=assembly_config)

if transcript.status == TranscriptStatus.error:
    raise RuntimeError(f"Transcription failed: {transcript.error}")

print(f"Transcript ID: {transcript.id}")

print(f"\nFull Transcript:\n\n{transcript.text}")


for utterance in transcript.utterances:
    print(f"Speaker {utterance.speaker}: {utterance.text}")