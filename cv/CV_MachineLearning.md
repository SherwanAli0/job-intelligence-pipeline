# SHERWAN ALI
**Machine Learning Engineer · Junior**
Bochum, Germany · sherwan2.ali@gmail.com
github.com/SherwanAli0 · linkedin.com/in/sherwan-ali · German residence permit, full work authorization

> Use this version for: **ML Engineer, Applied Scientist, MLOps, Deep Learning, Computer Vision, NLP, Research Engineer** roles.

## PROFILE
Computer Engineering graduate (Üsküdar University, Istanbul, 07.2026, final grade 1.8 German scale / GPA 3.45) with hands-on model training, evaluation and reproducibility depth. Runs open-source LLMs and classical ML pipelines end to end — data, training, logits/metrics, cross-validation, CI — in Python with PyTorch, scikit-learn and XGBoost, containerized with Docker and automated with GitHub Actions. English C1, German B1. Available immediately.

## PROJECTS
**Reproducibility Audit & Extension of CSRBoost** (graduation thesis, solo) · github.com/SherwanAli0/csrboost-audit
Python, scikit-learn, imbalanced-learn, LightGBM, pandas, NumPy; Docker, Fly.io.
Rebuilt CSRBoost (IEEE Access 2025) and evaluated 143 reported results across 15 datasets under one standardized protocol: only 29% reproduced. Identified the undisclosed evaluation choices needed to recover 143 of 143, isolating protocol sensitivity as the cause. Then designed **ERF-CSRBoost**, which selects its strategy per dataset on a held-out validation split, and **beat the published method on 14 of 15 datasets** (mean average precision +0.090) over 100-fold cross-validation. Deployed part of the work as a live data-leakage auditor at **fixyourdataset.fly.dev**.

**HalluScope — LLM Hallucination Detection** (5-author team) · github.com/SherwanAli0/HalluScope
Python, PyTorch, Transformers, spaCy. Owned the model-generation stage: ran LLaMA-3-8B, Qwen2-7B and Gemma-2-9B over 297 prompts, extracted token logits, computed per-sentence Shannon entropy; reproduced the central claim across all 9 model-dataset cells within ~0.02 nats. 12-test CI suite.

**FUS Recommender System Replication** (4-author team) · github.com/SherwanAli0/Recommender-System-Paper-Replication
Python, NumPy, scikit-learn, GitHub Actions. Implemented collaborative-filtering and FUS models on MovieLens 100k under 10-fold cross-validation; reproduced the headline ranking and matched MAE to four decimals (0.7025 vs 0.703). 12-test CI suite.

**Job Hunter** (solo) · github.com/SherwanAli0/job-hunter
Python, Anthropic API, LangGraph, AWS (ECS Fargate, S3, SSM, EventBridge, CloudWatch, ECR, IAM), Docker, GitHub Actions CI/CD.
Production agentic pipeline: ingests 8,000+ postings per run from 28+ sources (7 ATS APIs, government and aggregator APIs, RSS feeds), scores them 0–100 with Claude structured outputs, and emails a ranked daily digest. Orchestrated as a LangGraph state machine and run as a scheduled ECS Fargate task — chosen after measuring a 40-minute runtime against Lambda's 15-minute ceiling — with S3 state, SSM secrets and CloudWatch cost metrics. Batch API and prompt caching hold it at **$0.06 per run (measured)**. 208-test CI suite.

## EXPERIENCE
**Software and AI Intern — iseremo GmbH, Düsseldorf** · 04.2026 – 06.2026
- Built and tested AI/ML features in Python and FastAPI: integrated model APIs, containerized with Docker, worked on prompt generation and system-prompt versioning.
- Ran tests, error analyses and documentation with development tooling; fixed live bugs via REST API and Git.

**IT Support and Web Management Intern — Salam Institute for Peace and Justice (remote)** · 12.2024 – 08.2025
- Co-led a full WordPress migration/redesign for a multi-country nonprofit with zero downtime; evaluated 15+ firms; translated requirements into technical specs.

## TECHNICAL SKILLS
- **ML / DL:** PyTorch, TensorFlow, scikit-learn, XGBoost, imbalanced-learn; CNNs, transformers, transfer learning, computer vision, hyperparameter tuning, cross-validation, model evaluation, ensemble/boosting methods.
- **LLMs:** open-source LLMs (LLaMA, Qwen, Gemma), Hugging Face Transformers, token-level features, LLM evaluation, RAG.
- **Engineering:** Python, FastAPI, Docker, Git, GitHub Actions (CI/CD), Linux, Jupyter.
- **Data:** SQL, Pandas, NumPy, Seaborn.

## EDUCATION
**B.Sc. Computer Engineering — Üsküdar University, Istanbul** · graduated 07.2026 · final grade 1.8 (German scale) / GPA 3.45. Programme delivered in English.
Coursework: Machine Learning, Deep Learning, Computer Vision, Statistics, Data Mining.

## CERTIFICATIONS
IBM AI Engineering Professional Certificate (09.2025) · Google Advanced Data Analytics Professional Certificate (03.2026) · Databases and SQL for Data Science with Python (04.2026)

## LANGUAGES
Arabic (native) · Kurdish (native) · Turkish (C1) · English (C1) · German (B1)
