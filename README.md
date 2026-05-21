# UmaLLM

A standalone LLM translation API server, spun off from [Sugoi Toolkit](https://www.patreon.com/mingshiba) by MingShiba. Meant as an alternative to using Sugoi Offline for the funny horse game. Works with Hachimi to retrieve and translate stories in-game.

While LLMs still are still not perfect at translating, they're come a long way and are semi-readable now, especially when compared to Google Translate or DeepL. This project also isn't meant to be a replacement for UmaTL. Human translations will be better 99% of the time. This is more of a tool for those that want something translated ASAP

<p align="center">
  <img width="498" height="281" src="https://github.com/user-attachments/assets/6eb04525-a4fd-450c-beea-b40e10a3d9f7" />
</p>

## AI Disclaimer

Heavily vibe-coded with the assistance of Qwen3.6-27B. This project is mainly a byproduct of me experimenting with the b9180 MTP feature.

## What Does It Do?

Sits on a local port, waits for translation requests, and sends them off to your LLM of choice. Drop it in front of any app that speaks Sugoi Toolkit's translation API protocol, and it'll handle the rest.

Features:
- **Batch translation** - sends all lines in one LLM call for better consistency (Only active when parallel workers=1)
- **Parallel chunked translation** - Optional feature to split work across multiple workers for faster throughput
- **Character memory** - keeps track of character names, nicknames, and context so translations stay consistent (Appended to the system prompt)
- **Retry logic** - if the LLM messes up the output, it retries with a more detailed prompt
- **Format recovery** - handles truncated responses, numbered lists, markdown fences, and other LLM quirks

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
| `HTTP_port_number` | Port the server listens on (default: 14368) |
| `model_name` | LiteLLM-compatible model name, refer to [LiteLLM Providers](https://docs.litellm.ai/docs/providers) for model names|
| `api_server` | Your LLM API endpoint |
| `api_key` | API key (or `null` for local) |
| `system_prompt` | The translation instructions sent to the LLM |
| `context_lines` | How many previous lines to keep in conversation history (0 = none) |
| `temperature`, `top_p`, `top_k`, `min_p` | Sampling parameters |
| `repetition_penalty` | Repetition penalty (for supported backends) |
| `max_tokens` | Max tokens per LLM response |
| `parallel_workers` | **1** = single batch mode (all lines in one call). **>1** = parallel mode (lines split into chunks) |
| `chunk_size` | Lines per chunk when using parallel mode (ignored when `parallel_workers` = 1) |
| `max_retries` | How many times to retry if the LLM returns bad output, The worse the LLM, the higher this needs to be. If retry attempts go past this value, outputs 'Error'|

### Translation Modes

- **Single batch** (`parallel_workers: 1`) - sends everything to the LLM in one shot, similar to how it works with Sugoi Offline. Best for consistency and accuracy, but might be slower than parallel depending on your GPU, especially on events with lots of text

- **Parallel** (`parallel_workers: <1`, `chunk_size: <1`) - splits lines into chunks and processes them across multiple workers. Good for GPUs that can handle parallel requests or for users paying for API keys. `context_lines` looks in both directions (If context lines is 3, then the 3 raw texts before and after the current message are sent to the LLM for context.)

- For an RTX 5090, I've found `parallel_workers: 5` and `chunk_size: 15` to be the sweet spot. Using gemma-4-26b-a4b-it@q4_k_m, This setting usually translates almost every event in 4-10 seconds, and for large walls of text, it takes around 15 seconds. The good news is Hachimi caches auto translation, which means you can just set Auto Translate to on and enable Auto Play and read a Trainee's story with minimal interruptions on the next training session.

## Character Memory

I've included a character memory file on `data/character_memory.json` with character names, nicknames, and notes. The server will include relevant character info in each translation prompt so the LLM knows how to handle names and personalities.

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

Derived from [Sugoi Toolkit](https://www.patreon.com/mingshiba) by MingShiba. This repository does not contain proprietary model weights

Hachimi develolpers for the Auto Translate feature