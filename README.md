# 🧬 LLM DNA Explorer

> A Community-Driven Platform for Visualizing the Genetic Fingerprints of Large Language Models

[![MIT License](https://img.shields.io/badge/License-MIT-green.svg)](https://choosealicense.com/licenses/mit/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

## DistDNA v2 backend

A versioned backend adds Linear/RBF nearest-reference identification and separates
v1/v2 Community Lab proposals. Run `npm ci && npm run server`; a four-checkpoint
example bank is included. Larger compatible banks use `DISTDNA_DATA_DIR`.
See [API, protocol and validation](docs/DISTDNA.md).

## 🌟 Overview

LLM DNA Explorer is an interactive web application that visualizes the "DNA" (behavioral fingerprints) of Large Language Models. Using the **RepTrace** methodology, we extract unique signatures from model responses and project them into a navigable 2D space—the **DNA Galaxy**.

This platform enables researchers and enthusiasts to:
- 🔭 **Explore** the landscape of 300+ LLMs in an interactive scatter plot
- 🔬 **Compare** models to find genetic similarities and differences
- 🗳️ **Propose** new models for analysis and vote on priorities
- 📊 **Analyze** DNA distances and view side-by-side comparisons

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                         DNA Explorer                            │
├─────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌───────────┐  ┌──────────┐ │
│  │   Galaxy    │  │ Community   │  │ Workbench │  │  About   │ │
│  │  (Home)     │  │    Lab      │  │           │  │          │ │
│  │             │  │             │  │           │  │          │ │
│  │ • Scatter   │  │ • Propose   │  │ • Compare │  │ • Method │ │
│  │   Plot      │  │   Models    │  │   Models  │  │ • Paper  │ │
│  │ • Search    │  │ • Vote      │  │ • Rank by │  │ • Contact│ │
│  │ • Filter    │  │ • Track     │  │   Distance│  │          │ │
│  └─────────────┘  └─────────────┘  └───────────┘  └──────────┘ │
├─────────────────────────────────────────────────────────────────┤
│                    dna_database.json (Static)                   │
│                    Supabase (Community Data)                    │
└─────────────────────────────────────────────────────────────────┘
```

### Running the Development Server

The recommended way to run the project is using the clean restart script, which ensures a fresh state by clearing the Vite cache and killing any stale server processes.

**On Windows (WSL):**
```bash
# Recommended: Full reset and start
wsl bash scripts/restart_clean.sh

# Standard start:
wsl bash scripts/start_dev.sh
```

**On Linux/macOS:**
```bash
bash scripts/restart_clean.sh
```

Once started, open [http://localhost:5173](http://localhost:5173) in your browser.

> [!IMPORTANT]
> If you see a message saying "Port 5173 is in use, trying another one...", it means a ghost process is still holding the port. Always use `scripts/restart_clean.sh` to resolve this.

### Keyboard Shortcuts
- **Ctrl+0**: Reset zoom on Galaxy view
- **Interaction**: Organization labels and flavored areas are now interactive—hover to see model counts and details.

### Data Processing (Optional)

To regenerate the DNA database from raw data:
```bash
# On WSL/Linux
python scripts/process_data.py

# Or via npm
npm run build:data
```

## 🚀 Deployment

### Docker (Recommended)

1.  **Configure Environment**:
    Create a `.env` file in the root directory for production settings:
    ```ini
    # Production Domain
    BASE_URL=https://dna.xtra.science
    ```

    Update `server/.env` with your secure credentials:
    ```ini
    TURNSTILE_SECRET_KEY=your_turnstile_secret
    EMAIL_USER=your_email_address
    EMAIL_PASS=your_email_password
    ```

2.  **Run with Docker Compose**:
    ```bash
    ./deploy.sh
    ```

3.  **Manual Start**:
    ```bash
    docker compose build
    docker compose up -d
    ```

See `deployment_guide.md` for detailed instructions on setting up a VPS, configuring SSL with Cloudflare/Nginx, and managing the application.

## 📁 Project Structure

```
dna-web/
├── dna-out/                 # Raw DNA extraction data
│   ├── rand/                # Random prompt dataset
│   ├── hs_wg/               # Hellaswag + Winogrande
│   ├── squad_cqa/           # SQuAD + CommonsenseQA
│   └── _summary.json        # Aggregated model list
│
├── public/
│   └── dna_database.json    # Compiled DNA database
│
├── src/
│   ├── styles/
│   │   └── global.css       # Design system
│   │
│   ├── js/
│   │   ├── main.js          # App entry
│   │   ├── router.js        # Client-side routing
│   │   ├── data.js          # Data utilities
│   │   └── pages/           # Page controllers
│   │       ├── galaxy.js
│   │       ├── lab.js
│   │       ├── workbench.js
│   │       └── about.js
│   │
│   └── pages/               # HTML templates
│       ├── galaxy.html
│       ├── lab.html
│       ├── workbench.html
│       └── about.html
│
├── scripts/
│   └── build_database.js    # Data aggregation script
│
├── index.html               # Main entry
├── package.json
├── vite.config.js
└── README.md
```

## 📊 DNA Data Structure

Each model's DNA consists of a **128-dimensional signature vector** extracted using the RepTrace methodology:

```json
{
  "signature": [0.034, 0.028, -0.150, ...],  // 128 floats
  "metadata": {
    "model_name": "Qwen/Qwen2.5-7B-Instruct",
    "extraction_method": "text_embeddings_...",
    "probe_count": 100,
    "dna_dimension": 128
  }
}
```

The `dna_database.json` aggregates all models with pre-computed 2D projections:

```json
{
  "models": [
    {
      "id": "Qwen_Qwen2.5-7B-Instruct",
      "name": "Qwen/Qwen2.5-7B-Instruct",
      "family": "Qwen",
      "organization": "Qwen",
      "parameters": "7B",
      "signature": [...],
      "x": 12.34,  // t-SNE x coordinate
      "y": -5.67   // t-SNE y coordinate
    }
  ],
  "metadata": {
    "total_models": 305,
    "last_updated": "2026-01-31",
    "datasets": ["rand", "hs_wg", "squad_cqa"]
  }
}
```

## 🔧 Configuration

### Environment Variables

Create a `.env` file for Supabase integration:

```env
VITE_SUPABASE_URL=your-supabase-url
VITE_SUPABASE_ANON_KEY=your-anon-key
```

### Supabase Tables

For community features, create these tables in Supabase:

```sql
-- Model proposals
CREATE TABLE proposals (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  model_id TEXT NOT NULL UNIQUE,
  submitted_by TEXT,
  votes INTEGER DEFAULT 1,
  status TEXT DEFAULT 'pending',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Vote tracking
CREATE TABLE votes (
  id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
  proposal_id UUID REFERENCES proposals(id),
  voter_ip TEXT,
  created_at TIMESTAMPTZ DEFAULT NOW()
);
```

## 🎨 Pages

### 1. DNA Galaxy (Home)
Interactive 2D visualization of all model DNAs using t-SNE projection.
- **Color coding** by model family, organization, or architecture
- **Hover cards** showing model name, parameters, and nearest neighbor
- **Search** to locate specific models
- **Filters** for base/fine-tuned models

### 2. Community Lab
Crowdsource research priorities.
- **Propose** new models via HuggingFace ID
- **Vote** on models you want analyzed
- **Track** processing status (Pending → Scanning → Completed)

### 3. Workbench
Deep comparison tool.
- Select a **reference model** as anchor
- Choose a **comparison set** (e.g., "All 7B models")
- **Rank** by Euclidean distance (genetic similarity)
- **Visualize** DNA barcodes side-by-side

### 4. About
Methodology documentation based on the LLM DNA paper.

## 📚 Methodology

The DNA extraction uses **RepTrace** (Response Pattern Tracing):

1. **Probe Set**: 100 random word prompts sent to each model
2. **Response Encoding**: Responses embedded using Qwen3-Embedding-8B
3. **Aggregation**: Embeddings concatenated and reduced via random projection
4. **Signature**: Final 128-dimensional vector uniquely identifies the model

This signature captures the model's "behavioral fingerprint"—how it responds to diverse stimuli.

## 🤝 Contributing

We welcome contributions! Please see our [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

### Development Workflow

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/amazing-feature`
3. Commit changes: `git commit -m 'Add amazing feature'`
4. Push to branch: `git push origin feature/amazing-feature`
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙏 Acknowledgments

- [D3.js](https://d3js.org/) for visualization
- [Supabase](https://supabase.com/) for backend services
- The LLM DNA research team for the RepTrace methodology

---

**Made with 🧬 by the LLM DNA Team**
