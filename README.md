# PlacementBot

A simple chatbot that answers questions about your college placement report PDF using Retrieval-Augmented Generation (RAG).

## Setup

1. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure your Gemini API key** as an environment variable. In PowerShell, run:
   ```powershell
   $env:GEMINI_API_KEY = "your-gemini-api-key"
   ```
   Keep this terminal open and start the app from it. The key is not stored in the
   source code. You can create a key in Google AI Studio.

3. **Choose your report in the app.** In the sidebar, upload one or more placement
   report PDFs from your device, then select the report to analyze. This works
   both locally and when the app is deployed; no local file path is required.

4. **Run the Streamlit app**
   ```bash
   streamlit run app.py
   ```

5. Open the provided localhost URL (usually `http://localhost:8501`) in your browser.

## Deploy to Render

This project includes a `render.yaml` Blueprint for deploying the Streamlit app
as a Render Web Service.

1. Push the project to a GitHub, GitLab, or Bitbucket repository. Do not commit
   your Gemini API key.
2. In Render, choose **New → Blueprint**, connect the repository, and select
   `render.yaml`. Alternatively, create a Web Service with the build command
   `pip install -r requirements.txt` and start command
   `streamlit run app.py --server.address 0.0.0.0 --server.port $PORT`.
3. Provide `GEMINI_API_KEY` when Render prompts for the unsynced environment
   variable. You can also add it in the service's **Environment** settings.
4. Deploy. Open the generated `onrender.com` URL and upload your placement
   report PDFs from the sidebar.

The free Render instance can spin down after inactivity and may take about a
minute to wake up. Uploaded reports are held in memory for the active app
session; users should upload them again if the service restarts.

## How it works

- The Streamlit interface uses a responsive dashboard layout with report summary
  metrics, a question form, highlighted answers, and sortable result tables for
  data-backed queries.
- Reports are uploaded by the user and selected from the sidebar. The dashboard
  identifies the active report by its uploaded filename.
- Tables are extracted from every PDF page with `pdfplumber` and parsed into a
  pandas DataFrame, including the report's total-placement and CTC columns.
- Each row is turned into a descriptive text chunk.
- Per-program placement counts are extracted from the report's branch columns,
  so questions about the company with the most placements from a branch can be
  answered directly from that branch's counts.
- The top‑k chunks are retrieved by word overlap with the user's query.
- The retrieved context is sent to Gemini (`gemini-3.8-flash`) to generate an
  answer grounded in the placement data. Some common questions are answered
  directly from the table without an API call.

## Customization

- To use a different LLM (e.g., OpenAI GPT‑4o), replace the `generate_answer` function.
- Adjust chunking strategy or add metadata (e.g., source page) for better accuracy.
- The PDF parser expects the supported placement-report table layout. If a
  report has a different table structure, the app will explain that it could
  not find company placement rows.

## Notes

- The app caches PDF parsing by uploaded file content, so switching reports
  loads the correct data without reusing results from another PDF.
- If the PDF structure changes, you may need to adjust the table parsing logic.

Enjoy chatting with your placement data!