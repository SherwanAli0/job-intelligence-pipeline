# SHERWAN ALI
**AI / LLM Engineer · Working Student**
Bonn, Germany · sherwan2.ali@gmail.com
github.com/SherwanAli0 · linkedin.com/in/sherwan-ali · German residence permit, full work authorization

> Use this version for: **AI Engineer, LLM Engineer, GenAI, Applied AI, AI Agent, AI/Software Engineer** roles.

## PROFILE
Computer Engineering graduate (Üsküdar University, Istanbul, 07.2026, final grade 1.8 German scale / GPA 3.45) who recently built and shipped LLM features in production at iseremo GmbH — Anthropic and OpenAI APIs, FastAPI services, Docker, prompt and system-prompt versioning — and builds agentic tools of his own. Strong Python, PyTorch, and open-source-LLM (LLaMA / Qwen / Gemma) foundations. M.Sc. Artificial Intelligence student at the University of Bonn (from 10.2026), enrolled and eligible for Werkstudent roles. English C1, German B1. Available up to 20 h/week during the semester, more during semester breaks.

## EXPERIENCE
**Software and AI Intern — iseremo GmbH, Düsseldorf** · 04.2026 – 06.2026
- Built and tested AI features in Python and FastAPI: integrated the Anthropic and OpenAI APIs, containerized with Docker, and worked on prompt generation and system-prompt versioning.
- Used the WordPress REST API and Git to fix live bugs in an existing codebase; improved bilingual (DE/EN) SEO/GEO.
- Ran tests, error analyses and documentation, using development tooling in the daily workflow.

**IT Support and Web Management Intern — Salam Institute for Peace and Justice (remote)** · 12.2024 – 08.2025
- Co-led a full WordPress migration and redesign for a multi-country nonprofit with zero downtime.
- Built a vendor-evaluation framework covering 15+ firms; translated business needs into technical specs.

## PROJECTS
**HalluScope — LLM Hallucination Detection** (5-author team) · github.com/SherwanAli0/HalluScope
Python, PyTorch, Transformers, spaCy. Ran LLaMA-3-8B, Qwen2-7B and Gemma-2-9B over 297 prompts, extracted token logits, computed per-sentence Shannon entropy; reproduced the central claim across all 9 model-dataset cells within ~0.02 nats. 12-test CI suite.

**Job Hunter** (solo) · github.com/SherwanAli0/job-hunter
Python, Anthropic API, LangGraph, AWS (ECS Fargate, S3, SSM, EventBridge, CloudWatch, ECR, IAM), Docker, GitHub Actions CI/CD.
Production agentic pipeline: ingests 8,000+ postings per run from 28+ sources (7 ATS APIs, government and aggregator APIs, RSS feeds), scores them 0–100 with Claude structured outputs, and emails a ranked daily digest. Orchestrated as a LangGraph state machine and run as a scheduled ECS Fargate task — chosen after measuring a 40-minute runtime against Lambda's 15-minute ceiling — with S3 state, SSM secrets and CloudWatch cost metrics. Batch API and prompt caching hold it at **$0.06 per run (measured)**. 208-test CI suite.

**Reproducibility Audit & Extension of CSRBoost** (graduation thesis, solo) · github.com/SherwanAli0/csrboost-audit
Rebuilt an IEEE Access 2025 imbalanced-classification method and reproduced only 42 of 143 reported numbers (29%); recovered 143/143 within 3% error by identifying the undisclosed evaluation choices. Built ERF-CSRBoost (random-forest ensemble behind CSRBoost's balancing front end): across 20 datasets under 100-fold CV it wins on 17, average precision +0.064, p = 0.00032 (Wilcoxon). Losses reported openly. Live tool: fixyourdataset.fly.dev.

**FUS Recommender System Replication** (4-author team) · github.com/SherwanAli0/Recommender-System-Paper-Replication
MovieLens 100k, 10-fold cross-validation; matched published MAE to four decimals (0.7025 vs 0.703).

## TECHNICAL SKILLS
- **LLM & GenAI:** Anthropic API, OpenAI API, Hugging Face Transformers, open-source LLMs (LLaMA, Qwen, Gemma), prompt engineering, system-prompt versioning, RAG, agent workflows, structured outputs, LLM evaluation, hallucination detection.
- **ML:** PyTorch, TensorFlow, scikit-learn, XGBoost, imbalanced-learn; transformers, computer vision.
- **Backend & Infra:** Python, FastAPI, REST APIs, Docker, Git, GitHub Actions (CI/CD), Node.js, React, TypeScript, Linux, Jupyter.
- **Data:** SQL, Pandas, NumPy, Seaborn.

## EDUCATION
**M.Sc. Artificial Intelligence — University of Bonn** · from 10.2026

**B.Sc. Computer Engineering — Üsküdar University, Istanbul** · graduated 07.2026 · final grade 1.8 (German scale) / GPA 3.45. Programme delivered in English.

## CERTIFICATIONS
IBM AI Engineering Professional Certificate (09.2025) · Google Advanced Data Analytics Professional Certificate (03.2026) · Databases and SQL for Data Science with Python (04.2026)

## LANGUAGES
Arabic (native) · Kurdish (native) · Turkish (C1) · English (C1) · German (B1)
