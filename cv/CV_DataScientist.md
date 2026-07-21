# SHERWAN ALI
**Data Scientist · Junior**
Bochum, Germany · sherwan2.ali@gmail.com
github.com/SherwanAli0 · linkedin.com/in/sherwan-ali · German residence permit, full work authorization

> Use this version for: **Data Scientist, Data Analyst, BI Analyst, Analytics Engineer, Quantitative Analyst** roles.

## PROFILE
Data-focused Computer Engineering graduate (Üsküdar University, Istanbul, 07.2026, final grade 1.8 German scale / GPA 3.45) with strong statistical and experimentation grounding. Comfortable across the full analysis loop — hypothesis, study design, cross-validation, metric evaluation and reproducible reporting — with SQL, Python and Google-certified data-analytics tooling (Tableau, regression, statistics). English C1, German B1. Available immediately.

## PROJECTS
**Reproducibility Audit & Extension of CSRBoost** (graduation thesis, solo) · github.com/SherwanAli0/csrboost-audit
Python, scikit-learn, imbalanced-learn, LightGBM, pandas, NumPy; Docker, Fly.io.
Rebuilt CSRBoost (IEEE Access 2025) and evaluated 143 reported results across 15 datasets under one standardized protocol: only 29% reproduced. Identified the undisclosed evaluation choices needed to recover 143 of 143, isolating protocol sensitivity as the cause. Then designed **ERF-CSRBoost**, which selects its strategy per dataset on a held-out validation split, and **beat the published method on 14 of 15 datasets** (mean average precision +0.090) over 100-fold cross-validation. Deployed part of the work as a live data-leakage auditor at **fixyourdataset.fly.dev**.

**FUS Recommender System Replication** (4-author team) · github.com/SherwanAli0/Recommender-System-Paper-Replication
Python, NumPy, scikit-learn, GitHub Actions. Replicated an IEEE Access 2026 paper on MovieLens 100k under 10-fold cross-validation; owned the collaborative-filtering and FUS implementations; reproduced the headline ranking and matched MAE to four decimals (0.7025 vs 0.703). Recommender systems, offline evaluation, statistical validation.

**HalluScope — LLM Evaluation** (5-author team) · github.com/SherwanAli0/HalluScope
Python, PyTorch, spaCy. Quantitative evaluation study — computed per-sentence Shannon entropy over model outputs and matched published values to ~0.02 nats across 9 model-dataset cells. Measurement, metrics, statistical comparison.

**Job Hunter** (solo) · github.com/SherwanAli0/job-hunter
Python, Anthropic API, LangGraph, AWS (ECS Fargate, S3, SSM, EventBridge, CloudWatch, ECR, IAM), Docker, GitHub Actions CI/CD.
Production agentic pipeline: ingests 8,000+ postings per run from 28+ sources (7 ATS APIs, government and aggregator APIs, RSS feeds), scores them 0–100 with Claude structured outputs, and emails a ranked daily digest. Orchestrated as a LangGraph state machine and run as a scheduled ECS Fargate task — chosen after measuring a 40-minute runtime against Lambda's 15-minute ceiling — with S3 state, SSM secrets and CloudWatch cost metrics. Batch API and prompt caching hold it at **$0.06 per run (measured)**. 208-test CI suite.

## EXPERIENCE
**Software and AI Intern — iseremo GmbH, Düsseldorf** · 04.2026 – 06.2026
- Built and tested data/AI features in Python and FastAPI; worked with databases, ran tests, error analyses and documentation.

**IT Support and Web Management Intern — Salam Institute for Peace and Justice (remote)** · 12.2024 – 08.2025
- Co-led a WordPress migration/redesign for a multi-country nonprofit; built a vendor-evaluation framework over 15+ firms; translated requirements into technical specs.

## TECHNICAL SKILLS
- **Analysis & Statistics:** hypothesis testing, regression, cross-validation, experiment design, A/B-style evaluation, imbalanced-data methods, metric design.
- **Languages & Data:** Python, SQL, Pandas, NumPy, Seaborn, Tableau (Google Advanced Data Analytics), Jupyter.
- **Machine Learning:** scikit-learn, XGBoost, imbalanced-learn; classification, regression, recommender systems, model evaluation.
- **Also:** PyTorch / TensorFlow, LLMs, Docker, Git, GitHub Actions (CI/CD).

## EDUCATION
**B.Sc. Computer Engineering — Üsküdar University, Istanbul** · graduated 07.2026 · final grade 1.8 (German scale) / GPA 3.45. Programme delivered in English.
Coursework: Statistics, Data Mining, Machine Learning, Database Systems, Deep Learning, Computer Vision.

## CERTIFICATIONS
Google Advanced Data Analytics Professional Certificate (03.2026) · Databases and SQL for Data Science with Python (04.2026) · IBM AI Engineering Professional Certificate (09.2025)

## LANGUAGES
Arabic (native) · Kurdish (native) · Turkish (C1) · English (C1) · German (B1)
