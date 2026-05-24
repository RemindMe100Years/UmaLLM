# UmaLLM

A standalone LLM translation API server, spun off from [Sugoi Toolkit](https://www.patreon.com/mingshiba) by MingShiba. Meant as an alternative to using Sugoi Offline for the funny horse game. Works with Hachimi to retrieve and translate stories in-game.

While LLMs still are still not perfect at translating, they've come a long way and are semi-readable now, especially when compared to Google Translate or DeepL. This project also isn't meant to be a replacement for UmaTL. Human translations will be better 99% of the time. This is more of a tool for those that want something translated ASAP.

<p align="center">
  <img width="498" height="281" src="https://github.com/user-attachments/assets/4fcbda9c-0e47-4765-8d3e-5657ff130957" />
</p>

## AI Disclaimer

Heavily vibe-coded with the assistance of Qwen3.6-27B. This project is mainly a byproduct of me experimenting with the b9180 MTP feature.

## What Does It Do?

Sits on a local port, waits for translation requests, and sends them off to your LLM of choice through LiteLLM.

LLM-translated output is sent back to the game in JSON format and displayed in-game

Features:
- **Structured output** - Uses JSON schema to constrain LLM responses. Your only enemy now is LLM hallucination.
- **JSON validation** - Verifies response structure before processing, catches missing fields and type mismatches in case structured output somehow fails
- **Batch translation** - Can send all lines in one LLM call for better consistency (active when parallel workers = 1), this is how the default 'Auto Translate Stories' works.
- **Parallel chunked translation** - Splits work across multiple workers for faster throughput (but slightly increases the risk of bad output)
- **Context-aware chunking** - Context lines can be included. Moderately increases the risk of LLM hallucination but gives context to previous lines if using parallel TL
- **Character memory** - Keeps track of character names, nicknames, and context so translations stay consistent (appended to the system prompt)
- **Free-form languages** - No hardcoded language list. Set output languages to any string in settings
- **Selective retry logic** - Full retry on count mismatch, or fixes individual trivial lines without retranslating the whole chunk
- **Line-level fallback** - If chunk retries are exhausted, falls back to translating lines individually as a last resort. If this also fails, freezes the game (Intentional because cache works against you here. You would see the error placeholder everytime you see the event in subsequent encounters).
- **Format recovery** - Handles truncated responses, numbered lists, markdown fences, malformed JSON, and other LLM quirks
- **File logging** - Timestamped logs in `logs/` folder, hardcoded to keep 5 most recent session logs, splitting at 10MB.
- **(EN ONLY) Optional Jamdict integration** - Might help for low parameter LLMs since TL'ed output is checked against the raw text. If output vastly differs (Apple -> Orange), then a retry is called.

## Known Issues

- **Character names exceeding the name box** - AFAIK there's no way to differentiate between regular dialogue and names from the JSON Hachimi sends. Instructing the LLM to shorten names may lead to loss of quality.

- **Trainer dialogue extending beyond the screen** - Text that is only a few characters wide in Japanese can expand into a full sentence in English, causing the Trainer's dialogue to spill out of the screen.

- **No safeguard for mispelled words** - 

## Installation

1. Have Python 3.12+ installed and on PATH

2. Run setup.bat to install dependencies (botocore is optional, LiteLLM seems to require it if you use AWS Bedrock/SageMaker)

3. Run configure.bat to configure LLM + Other settings. Default settings points to LM Studio running gemma-4-26b-a4b-it@q4_k_m, with instructions to localize dialogue from Japanese to English

4. Configure Hachimi to point to port 14368:
   - Open `Game Root/hachimi/config.json` and set:
     ```json
     "sugoi_url": "http://127.0.0.1:14368"
     ```
   - In-game, open Hachimi's Config Editor > General and check **Auto Translate Stories**

<p align="center">
  <img src="images/AutoTL.png" alt="Check this setting to allow Hachimi to send translation requests" width="400">
</p>

5. Run launch.bat to start the translation server

6. Start the game and Hachimi will send translation Requests to port 14368 when it encounters any Japanese dialogue.

7. ???

8. Profit

## Settings

| Setting | Description |
|---|---|
| `HTTP_port_number` | Port the server listens on (default: `14368`) |
| `model_name` | LiteLLM-compatible model name, refer to [LiteLLM Providers](https://docs.litellm.ai/docs/providers) for model names |
| `api_server` | Your LLM API endpoint |
| `api_key` | API key (or `null` for local) |
| `system_prompt` | The translation instructions sent to the LLM |
| `context_lines` | How many previous lines to keep in conversation history. Recommend to set this to 0 to minimize hallucinations. (default: `2`) |
| `temperature`, `top_p`, `top_k`, `min_p` | Sampling parameters (default: `0.6`, `0.95`, `64`, `0.05`) |
| `repetition_penalty` | Repetition penalty (for supported backends) (default: `1.1`) |
| `frequency_penalty` | Frequency penalty to reduce repetitive output (default: `0.5`) |
| `presence_penalty` | Presence penalty to encourage varied output (default: `0.5`) |
| `max_tokens` | Max tokens per LLM response. If your translations are getting cut off, increasing this might help (default: `2048`)  |
| `parallel_workers` | **1** = single batch mode (all lines in one call). **>1** = parallel mode (lines split into chunks) (default: `3`) |
| `chunk_size` | Number of lines per chunk when using parallel mode (ignored when `parallel_workers` = 1). (default: `15`) |
| `max_retries` | How many times to retry if the LLM returns bad output. The worse the LLM, the higher this needs to be. If chunk retries are exhausted, falls back to translating lines individually. (default: `3`) |
| `append_all_characters` | Only relevant if using parallel TL. If `true`, scans the full batch once. Any characters that matched are added to the current glossary, and that glossary is shared with all chunks. Recommended to have this on when using parallel translation. (default: `false`) |
| `jamdict_sanity_check` | If `true`, runs translated output through Jamdict to verify Japanese terms were actually translated. Set to false if output language is not English. Otherwise, every output will be flagged. (default: `false`) |
| `output_language` | Target language for translations (default: `English`).|
| `preserve_honorifics` | We use a simple logic to append honorific to the character names in glossary to make the LLM more consistent. If you want this feature off, you would also need to remove all instructions pertaining to the use of honorifics in the system prompt. (default: `true`).|

### Translation Modes

- **Single batch** (`parallel_workers: 1`) - sends everything to the LLM in one shot, similar to how it works with Sugoi Offline. Best for consistency and accuracy, but might be slower than parallel depending on your GPU, especially on events with lots of text

- **Parallel** (`parallel_workers: >1`, `chunk_size: <N>`) - splits input into character-sized chunks and processes them across multiple workers. Good for GPUs that can handle parallel requests or for users paying for API keys. `context_lines` looks in both directions (if context lines is 3, then the 3 raw texts before and after the current message are sent to the LLM for context)

- For an RTX 5090, I've found `parallel_workers: 5` and `chunk_size: 15` to be the sweet spot. Using gemma-4-26b-a4b-it@q4_k_m, this setting usually translates almost every event in 4-10 seconds, and for large walls of text it takes around 15 seconds. The good news is Hachimi caches auto translation, which means you can just set Auto Translate to on and enable Auto Play and read a Trainee's story with minimal interruptions the next time you encounter the same event.

## Character Memory

I've included a character memory file on `data/character_memory.json` with character names, nicknames, and notes. The server will include relevant character info in each translation prompt so the LLM knows how to handle names and personalities.

All string fields support `{input_language}` and `{output_language}` placeholders, resolved at runtime. This lets you write notes like "makes sense in {input_language}" that adapt to any language pair without editing the file.

Example:
```json
{
  "characters": {
    "スペシャルウィーク": {
      "name": "Special Week",
      "nickname": ["Spe (スペ)"],
      "gender": "female",
      "notes": "Speaks with a rustic, country accent."
    }
  }
}
```
In scenario above, with `preserve_honorifics` set to `true`, JP text スペちゃん would get added to glossary as Spe-chan. Same with スペさん and スペ先輩. These get added to the glossary as Spe-san and Spe-senpai. If false, it just adds スペ everytime, no matter the honorific used.

## Jamdict Integration

The server can optionally use [Jamdict](https://pypi.org/project/jamdict/) to verify translations. When `jamdict_sanity_check` is enabled in settings, the server runs the translated output through Jamdict to check that Japanese terms were actually translated and not left as romaji or untranslated kanji.

I haven't done extensive testing if this actually helps, but theoretically, it should help when you are using an old LLM model like [VNTL Llamma](https://huggingface.co/lmg-anon/vntl-llama3-8b-v2-gguf) or a model that is not good at translating from English to Japanese. Additional latency is in the milisecond range.

## Logging

The server writes timestamped log files to the `logs/` folder. Each server run creates a new log file, and the 5 most recent are kept. Logs include translation timing, retry attempts, and RAW/TRN pairs for debugging/comparing raw JP text to TL'ed.

## Configuration

Included is a batch file **configure.bat** for easier configuration of the features listed above. Designed to be user friendly to minimize the times you have to edit settings.json and character_memory.json manually.

## Examples

<p align="center">
  <img src="images/Sample1.jpg">
</p>

<p align="center">
  <img src="images/Sample2.jpg">
</p>

<p align="center">
  <img src="images/Sample3.jpg">
</p>

<p align="center">
  <img src="images/Sample4.jpg">
</p>

<p align="center">
  <img src="images/Sample5.jpg">
</p>

<p align="center">
  <img src="images/Sample6.jpg">
</p>

## Credits

Derived from [Sugoi Toolkit](https://www.patreon.com/mingshiba) by MingShiba. This repository does not contain proprietary model weights.

neocl for [jamdict_data](https://github.com/neocl/jamdict_data). This is the prebuilt Jamdict database that is downloaded when the user opts to install [Jamdict](https://pypi.org/project/jamdict/)

Hachimi developers for the Auto Translate feature.
