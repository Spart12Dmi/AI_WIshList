# AI_WIshList
## Local product-search assistant

This project will run its API and agent workflow locally. Ollama runs the local
language model; the Python application uses LangGraph to coordinate product
search, extraction, validation, and presentation to the web app.

### 1. Create the CUDA environment

From Command Prompt or PowerShell in this folder, run:

```bat
scripts\setup_conda_env.bat
```

The script creates a Conda environment named `local-product-search`, installs
the official CUDA 12.8 PyTorch wheel set, installs the project libraries, and
prints a GPU verification result. It uses Python 3.11.

To remove and build the environment again:

```bat
scripts\setup_conda_env.bat --recreate
```

### 2. Install Ollama and a small model

Install [Ollama for Windows](https://ollama.com/download/windows), then open a
new PowerShell window. The default model is `llama3.2:3b`. Llama does not have
an exact 2B model in this family; this 3B version remains suitable for an RTX
4060 with 8 GB VRAM:

```bat
ollama pull llama3.2:3b
```

Or, after Ollama is installed, let the setup script download it:

```bat
scripts\setup_conda_env.bat --pull-model
```

To install the Chromium runtime used to render JavaScript shop pages, run once:

```bat
scripts\setup_conda_env.bat --install-browser
```

Or use the dedicated script after setup:

```bat
scripts\install_browser.bat
```

For a smaller near-2B alternative with good structured tool calls, choose Qwen
3 1.7B instead:

```bat
scripts\setup_conda_env.bat --model qwen3:1.7b --pull-model
```

### 3. Activate the environment

```bat
conda activate local-product-search
```

### 4. Run the product-search GUI

Start the local FastAPI server:

```bat
scripts\run_app.bat
```

Open **http://127.0.0.1:8000** in your browser. Enter a product name, brand,
model, colour, or size, then select the shopping region. The selected market
guides the web search toward its local language and shops. The app will show only candidates for which it could
read a product price from the shop page, along with the product photo and the
link back to the shop.

Offers stream into the page one by one as individual product pages finish
verification. They are provisional until the final semantic and regional check;
any generic or out-of-region cards are then removed automatically.

### Workflow

```text
Keyboard input
  → Query planner agent (local Ollama model; deterministic fallback)
  → Store discovery agent (finds up to 15 relevant shop domains)
  → Parallel store-search agents (each searches one store via search_store_catalog)
  → Product extraction agent (extract_product tool, parallel requests)
  → Chromium fallback for pages whose price is rendered by JavaScript
  → Semantic validation agent (local Llama rejects generic and out-of-region offers)
  → Ranking agent
  → Browser cards: photo, price, shop, availability, link
```

The first version uses public web search and Schema.org/OpenGraph metadata
provided by shop pages. It is intentionally defensive: it rejects private
network URLs, limits page downloads, and does not use page text as instructions
for the model. For a production commercial service, replace or supplement the
search tool with licensed merchant or affiliate APIs for the shops you support.
