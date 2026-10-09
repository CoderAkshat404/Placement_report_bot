import html
from io import BytesIO
from bisect import bisect_right
import pandas as pd, re, string
import pdfplumber
import streamlit as st
import os
import requests

st.set_page_config(
    page_title="Placement Report Chatbot",
    page_icon="🎓",
    layout="wide",
)

# ---------- CONFIG ----------
GEMINI_MODEL = "gemini-3.8-flash"
GEMINI_API_URL = (
    f"https://generativelanguage.googleapis.com/v1beta/models/"
    f"{GEMINI_MODEL}:generateContent"
)
PROGRAM_NAMES = [
    "B.Arch",
    "Bio-Medical",
    "Bio-Technology",
    "Chemical",
    "Civil Engineering",
    "CSE",
    "Electrical",
    "ECE",
    "IT",
    "Mechanical",
    "Metallurgical",
    "Mining",
    "ACM",
    "Physics",
    "Chemistry",
    "Maths",
    "M.Tech Geology",
    "M.Tech Chemical",
    "M.Tech Civil",
    "M.Tech Electrical",
    "M.Tech E&T&C",
    "M.Tech IT",
    "M.Tech Urban Planning",
    "M.Tech Mechanical",
]
PDF_COLUMNS = [
    "S.No",
    "Name of the Company",
    "Offer Type",
    "Sector",
    "Process Mode",
    "Process Date",
    *PROGRAM_NAMES,
    "Total Placed",
    "Highest CTC",
    "UG CTC",
    "ACM CTC",
    "MTech CTC",
]

# Users select reports from their own device, so the app also works when hosted.
st.sidebar.title("Placement reports")
uploaded_reports = st.sidebar.file_uploader(
    "Upload one or more placement reports",
    type=["pdf"],
    accept_multiple_files=True,
    help="Choose the reports you want to compare or switch between.",
)
if not uploaded_reports:
    st.sidebar.info("Download a report from the T&P statistics page, then upload it here.")
    st.title("Explore placement reports")
    st.markdown(
        """
        ### Get started

        Download a **company-visited placement report for any year** from the
        [NIT Raipur Training & Placement statistics page](https://tpo.nitrr.ac.in/stats.html),
        then upload the PDF using **Browse files** in the sidebar.

        You can upload multiple reports and select which year to explore.
        """
    )
    st.stop()

selected_report_index = st.sidebar.selectbox(
    "Choose a report",
    options=range(len(uploaded_reports)),
    format_func=lambda index: uploaded_reports[index].name,
)
selected_report = uploaded_reports[selected_report_index]

# ---------- 1. PDF -> DataFrame ----------
def pdf_to_df(pdf_content, pages=None):
    records = []
    try:
        with pdfplumber.open(BytesIO(pdf_content)) as pdf:
            selected_pages = pdf.pages
            if pages:
                first_page, last_page = (int(page) for page in pages.split("-", 1))
                if first_page < 1 or last_page < first_page or last_page > len(pdf.pages):
                    st.error(f"Invalid page range: {pages}")
                    return pd.DataFrame()
                selected_pages = pdf.pages[first_page - 1:last_page]
            for page in selected_pages:
                for table in page.find_tables():
                    table_rows = table.extract()
                    header_row_index = next(
                        (
                            index
                            for index, row in enumerate(table_rows)
                            if str(row[0] or "").strip().lower() == "s.no"
                            and "name of the company" in re.sub(
                                r"\s+", " ", str(row[1] or "").strip().lower()
                            )
                        ),
                        None,
                    )
                    if header_row_index is None:
                        continue

                    header_cells = table.rows[header_row_index].cells
                    if len(table_rows[header_row_index]) == 29:
                        # Earlier report layout: no sector/offer columns and a
                        # separate CSM column that is not part of PROGRAM_NAMES.
                        program_column_map = [
                            *zip(range(3, 16), PROGRAM_NAMES[:13]),
                            *zip(range(17, 24), PROGRAM_NAMES[16:23]),
                        ]
                        total_column_index = 24
                        highest_ctc_column_index = 25
                    else:
                        program_column_map = list(
                            zip(range(6, 28), PROGRAM_NAMES[:22])
                        )
                        final_group = header_cells[28]
                        if final_group is not None:
                            x0, top, x1, bottom = final_group
                            midpoint = (x0 + x1) / 2
                            program_column_map.extend(
                                [
                                    (28, PROGRAM_NAMES[22], (x0, top, midpoint, bottom)),
                                    (28, PROGRAM_NAMES[23], (midpoint, top, x1, bottom)),
                                ]
                            )
                        total_column_index = 30
                        highest_ctc_column_index = 31

                    normalized_program_columns = []
                    for item in program_column_map:
                        if len(item) == 2:
                            column_index, program_name = item
                            cell_bbox = header_cells[column_index]
                        else:
                            column_index, program_name, cell_bbox = item
                        if cell_bbox is not None:
                            normalized_program_columns.append(
                                (program_name, cell_bbox)
                            )
                    total_bbox = header_cells[total_column_index]
                    highest_ctc_bbox = header_cells[highest_ctc_column_index]

                    row_items = []
                    for row_index, row in enumerate(table_rows):
                        cells = list(row[:len(PDF_COLUMNS)])
                        cells.extend([None] * (len(PDF_COLUMNS) - len(cells)))
                        serial = str(cells[0] or "").strip()
                        company = str(cells[1] or "").strip()
                        if (
                            row_index <= header_row_index
                            or not re.fullmatch(r"\d+", serial)
                            or not company
                        ):
                            continue
                        row_bbox = table.rows[row_index].cells[0]
                        if row_bbox is None:
                            continue
                        if len(table_rows[header_row_index]) == 29:
                            record = {column: "" for column in PDF_COLUMNS}
                            record["S.No"] = serial
                            record["Name of the Company"] = company
                            record["Process Date"] = str(cells[2] or "").strip()
                        else:
                            record = {
                                column: str(value).strip() if value is not None else ""
                                for column, value in zip(PDF_COLUMNS, cells)
                            }
                        row_items.append((row_bbox, record))

                    row_tops = [row_bbox[1] for row_bbox, _ in row_items]
                    program_counts = [
                        {program_name: 0 for program_name in PROGRAM_NAMES}
                        for _ in row_items
                    ]
                    numeric_values = [{} for _ in row_items]
                    for word in page.extract_words(x_tolerance=1, y_tolerance=2):
                        row_position = bisect_right(row_tops, word["top"]) - 1
                        if row_position < 0:
                            continue
                        row_bbox = row_items[row_position][0]
                        if word["top"] >= row_bbox[3]:
                            continue
                        word_center = (word["x0"] + word["x1"]) / 2
                        for program_name, bbox in normalized_program_columns:
                            if (
                                re.fullmatch(r"\d+", word["text"])
                                and bbox[0] <= word_center < bbox[2]
                            ):
                                program_counts[row_position][program_name] += int(
                                    word["text"]
                                )
                        for column_name, bbox in (
                            ("Total Placed", total_bbox),
                            ("Highest CTC", highest_ctc_bbox),
                        ):
                            if (
                                bbox is not None
                                and re.fullmatch(r"\d+(?:\.\d+)?", word["text"])
                                and bbox[0] <= word_center < bbox[2]
                            ):
                                numeric_values[row_position].setdefault(
                                    column_name, word["text"]
                                )

                    for row_position, (_, record) in enumerate(row_items):
                        for name, count in program_counts[row_position].items():
                            record[name] = str(count)
                        record.update(numeric_values[row_position])
                        records.append(record)

    except pdfplumber.utils.exceptions.PdfminerException as exc:
        raise ValueError(
            "The selected file could not be read as a PDF. Choose a valid placement-report PDF."
        ) from exc
    except (OSError, ValueError) as e:
        raise ValueError(f"Failed to read the selected PDF: {e}") from e
    if not records:
        raise ValueError(
            "No company placement rows were found. Make sure the selected PDF "
            "uses the supported placement-report table format."
        )
    return pd.DataFrame.from_records(records, columns=PDF_COLUMNS)

# Load data (cache to avoid re-reading)
@st.cache_data
def load_data(pdf_content):
    return pdf_to_df(pdf_content)

try:
    df = load_data(selected_report.getvalue())
except ValueError as exc:
    st.error(str(exc))
    st.stop()

# ---------- 2. Create text chunks ----------
def row_to_text(row):
    parts = [f"Company: {row['Name of the Company']}"]
    fields = (
        ("Offer", "Offer Type"),
        ("Sector", "Sector"),
        ("Process mode", "Process Mode"),
        ("Total placed", "Total Placed"),
        ("Highest CTC", "Highest CTC"),
    )
    for label, column in fields:
        value = str(row[column]).strip()
        if value and value != "-":
            suffix = " LPA" if column == "Highest CTC" else ""
            parts.append(f"{label}: {value}{suffix}")
    for program in PROGRAM_NAMES:
        count = pd.to_numeric(row[program], errors="coerce")
        if pd.notna(count) and count > 0:
            parts.append(f"{program}: {int(count)}")
    return " - ".join(parts)

texts = df.apply(row_to_text, axis=1).tolist()
# Preprocess texts for retrieval: tokenized sets
def tokenize(text):
    text = text.lower()
    # Remove punctuation
    text = text.translate(str.maketrans('', '', string.punctuation))
    return set(text.split())
tokenized_texts = [tokenize(t) for t in texts]

# ---------- 3. Simple retrieval based on word overlap ----------
def retrieve(query, k=5):
    query_tokens = tokenize(query)
    scores = []
    for i, tokens in enumerate(tokenized_texts):
        # compute intersection size
        score = len(query_tokens & tokens)
        scores.append((score, i))
    # sort by score descending
    scores.sort(key=lambda x: x[0], reverse=True)
    # get top k indices
    top_indices = [idx for score, idx in scores[:k] if score > 0]
    if not top_indices:
        # fallback: return first k texts
        top_indices = list(range(min(k, len(texts))))
    return [texts[i] for i in top_indices]

# ---------- 4. Answer generation and structured result formatting ----------
BRANCH_PATTERNS = [
    ("M.Tech E&T&C", r"\be\s*&\s*t\s*&\s*c\b|electronics?\s+and\s+telecommunications?"),
    ("M.Tech Urban Planning", r"\burban\s+planning\b|\bplanning\b"),
    ("M.Tech Geology", r"\bgeology\b"),
    ("M.Tech Chemical", r"\bchemical\b|\bchem\b"),
    ("M.Tech Civil", r"\bcivil\b"),
    ("M.Tech Electrical", r"\belectrical\b|\belect\b"),
    ("M.Tech Mechanical", r"\bmechanical\b|\bmech\b"),
    ("M.Tech IT", r"\bit\b|\binformation\s+techn\w*"),
    ("Bio-Medical", r"\bbio[\s-]*medical\b|\bbiomedical\b"),
    ("Bio-Technology", r"\bbio[\s-]*technology\b|\bbiotechnology\b"),
    ("B.Arch", r"\bb\.?\s*arch(?:itecture)?\b|\barchitecture\b"),
    ("Civil Engineering", r"\bcivil(?:\s+engineering)?\b"),
    ("CSE", r"\bcse\b|\bcomputer\s+science(?:\s+and\s+engineering)?\b"),
    ("Electrical", r"\belectrical(?:\s+engineering)?\b"),
    ("ECE", r"\bece\b|electronics?\s*(?:and\s*)?communication"),
    ("IT", r"\bit\b|\binformation\s+techn\w*"),
    ("Mechanical", r"\bmechanical(?:\s+engineering)?\b"),
    ("Metallurgical", r"\bmetallurgical\b|\bmetallurgy\b"),
    ("Mining", r"\bmining\b"),
    ("Chemical", r"\bchemical(?:\s+engineering)?\b"),
    ("ACM", r"\bacm\b"),
    ("Physics", r"\bphysics\b"),
    ("Chemistry", r"\bchemistry\b"),
    ("Maths", r"\bmaths?\b|\bmathematics\b"),
]


def find_branch_column(question):
    q_lower = question.lower()
    mtech_requested = bool(re.search(r"\bm\.?\s*tech\b|\bmtech\b", q_lower))
    for column, pattern in BRANCH_PATTERNS:
        if column.startswith("M.Tech ") != mtech_requested:
            continue
        if re.search(pattern, q_lower):
            return column
    return None


def answer_table_for_question(question, dataframe):
    q_lower = question.lower()
    has_ctc_metric = any(
        term in q_lower for term in ("ctc", "salary", "package", "lpa")
    )
    branch_column = find_branch_column(question)
    asks_for_most = any(
        term in q_lower for term in ("most", "maximum", "highest", "largest", "took")
    )

    if branch_column and asks_for_most and not has_ctc_metric:
        values = pd.to_numeric(dataframe[branch_column], errors="coerce")
        if values.notna().any() and values.max() > 0:
            winners = dataframe.loc[values == values.max()]
            return winners[
                ["Name of the Company", branch_column, "Total Placed", "Offer Type", "Sector"]
            ].rename(
                columns={
                    "Name of the Company": "Company",
                    branch_column: f"{branch_column} placements",
                    "Total Placed": "Total placements",
                }
            )
        return None

    threshold_match = re.search(
        r"(>=|≥|>|greater than|more than|above|over|at least)\s*(\d+(?:\.\d+)?)",
        q_lower,
    )
    has_placement_metric = re.search(
        r"\b(hir(?:e|ed|es|ing)|placement|placed|student|people|recruit(?:ed|er)?|offer)\b",
        q_lower,
    )
    if threshold_match and (has_ctc_metric or has_placement_metric):
        column = "Highest CTC" if has_ctc_metric else "Total Placed"
        values = pd.to_numeric(dataframe[column], errors="coerce")
        threshold = float(threshold_match.group(2))
        inclusive = threshold_match.group(1) in {">=", "≥", "at least"}
        matching = dataframe.loc[values >= threshold if inclusive else values > threshold]
        if matching.empty:
            return None
        result_columns = ["Name of the Company", column, "Offer Type", "Sector"]
        labels = {
            "Name of the Company": "Company",
            "Highest CTC": "Highest CTC (LPA)",
            "Total Placed": "Total placements",
        }
        return matching[result_columns].rename(columns=labels)

    if "most" in q_lower and any(
        term in q_lower for term in ("student", "people", "hired", "took", "placement")
    ):
        values = pd.to_numeric(dataframe["Total Placed"], errors="coerce")
        if values.notna().any():
            winners = dataframe.loc[values == values.max()]
            return winners[
                ["Name of the Company", "Total Placed", "Highest CTC", "Offer Type", "Sector"]
            ].rename(
                columns={
                    "Name of the Company": "Company",
                    "Total Placed": "Total placements",
                    "Highest CTC": "Highest CTC (LPA)",
                }
            )
    return None


def generate_answer(context_chunks, question, dataframe):
    q_lower = question.lower()
    has_ctc_metric = any(
        term in q_lower for term in ("ctc", "salary", "package", "lpa")
    )
    branch_column = find_branch_column(question)

    asks_for_most = any(
        term in q_lower for term in ("most", "maximum", "highest", "largest", "took")
    )
    if branch_column and asks_for_most and not has_ctc_metric:
        values = pd.to_numeric(dataframe[branch_column], errors="coerce")
        if values.notna().any() and values.max() > 0:
            maximum = values.max()
            companies = dataframe.loc[
                values == maximum, "Name of the Company"
            ].tolist()
            return (
                f"The company with the most placements from {branch_column} is "
                f"{', '.join(companies)} with {int(maximum)} students."
            )
        return f"No placements from {branch_column} were found in the report."

    threshold_match = re.search(
        r"(>=|≥|>|greater than|more than|above|over|at least)\s*(\d+(?:\.\d+)?)",
        q_lower,
    )
    has_placement_metric = re.search(
        r"\b(hir(?:e|ed|es|ing)|placement|placed|student|people|recruit(?:ed|er)?|offer)\b",
        q_lower,
    )
    if threshold_match and (has_ctc_metric or has_placement_metric):
        threshold = float(threshold_match.group(2))
        inclusive = threshold_match.group(1) in {">=", "≥", "at least"}
        if has_ctc_metric:
            metric_column = "Highest CTC"
            metric_name = "highest CTC"
            multiplier = " LPA"
        else:
            metric_column = "Total Placed"
            metric_name = "students placed"
            multiplier = ""
        values = pd.to_numeric(dataframe[metric_column], errors="coerce")
        comparison = values >= threshold if inclusive else values > threshold
        companies = dataframe.loc[comparison, "Name of the Company"].tolist()
        operator = "at least" if inclusive else "more than"
        if companies:
            return (
                f"Companies with {operator} {threshold:g}{multiplier} "
                f"{metric_name}: {', '.join(companies)}."
            )
        return f"No company found with {operator} {threshold:g}{multiplier} {metric_name}."

    if "most" in q_lower and any(
        term in q_lower for term in ("student", "people", "hired", "took", "placement")
    ):
        values = pd.to_numeric(dataframe["Total Placed"], errors="coerce")
        if values.notna().any():
            maximum = values.max()
            companies = dataframe.loc[values == maximum, "Name of the Company"].tolist()
            return (
                f"The company with the most students placed is {', '.join(companies)} "
                f"with {int(maximum)} total placements."
            )
    # Pattern: how many offers did X make
    m = re.search(r'how many.*offers?.*did\s+([\w\s&.-]+)\s+make', q_lower)
    if m:
        company_name = m.group(1).strip()
        # find row
        mask = dataframe['Name of the Company'].str.contains(company_name, case=False, na=False)
        if mask.any():
            row = dataframe[mask].iloc[0]
            total = pd.to_numeric(row["Total Placed"], errors="coerce")
            if pd.notna(total):
                return (
                    f"The report lists {int(total)} students placed at "
                    f"{row['Name of the Company']}."
                )
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GEMINI_API_KEY is not set. Configure your Gemini API key and restart the app."
        )

    context = "\n".join(f"- {chunk}" for chunk in context_chunks)
    payload = {
        "contents": [
            {
                "parts": [
                    {
                        "text": (
                            "Answer the user's question using only the placement "
                            "data below. If the data does not contain the answer, "
                            "say so clearly. Do not invent facts.\n\n"
                            f"Placement data:\n{context}\n\n"
                            f"Question: {question}"
                        )
                    }
                ]
            }
        ],
        "generationConfig": {"temperature": 0.2},
    }

    try:
        response = requests.post(
            GEMINI_API_URL,
            params={"key": api_key},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise RuntimeError("Gemini did not respond within 30 seconds.") from exc
    except requests.HTTPError as exc:
        try:
            error_details = response.json().get("error", {})
            message = error_details.get("message")
        except (ValueError, AttributeError):
            message = None
        if not message:
            message = "Check that the API key and model are available to your project."
        raise RuntimeError(
            f"Gemini API error (HTTP {response.status_code}): {message}"
        ) from exc
    except requests.RequestException as exc:
        raise RuntimeError("Could not connect to the Gemini API.") from exc

    try:
        result = response.json()
    except ValueError as exc:
        raise RuntimeError("Gemini returned an invalid response.") from exc

    try:
        return result["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("Gemini returned no answer for this question.") from exc

# ---------- 5. Streamlit UI ----------
st.markdown(
    """
    <style>
    .stApp {
        background:
            radial-gradient(ellipse at 12% 0%, rgba(30, 110, 115, .22), transparent 34%),
            radial-gradient(ellipse at 92% 5%, rgba(88, 75, 155, .18), transparent 30%),
            #0b1220;
        color: #e8eef7;
    }
    [data-testid="stHeader"] { background: transparent; }
    [data-testid="stMainBlockContainer"] {
        max-width: 1120px;
        padding-top: 2.6rem;
        padding-bottom: 4rem;
    }
    .hero {
        padding: 1.7rem 1.9rem 1.45rem;
        margin-bottom: 1.25rem;
        border: 1px solid rgba(148, 163, 184, .18);
        border-radius: 22px;
        background: linear-gradient(120deg, rgba(18, 34, 51, .96), rgba(20, 29, 50, .9));
        box-shadow: 0 18px 55px rgba(0, 0, 0, .22);
    }
    .hero-kicker {
        color: #69d3c2;
        font-size: .74rem;
        font-weight: 750;
        letter-spacing: .15em;
        text-transform: uppercase;
    }
    .hero-title {
        margin: .4rem 0 .35rem;
        color: #f4f7fb;
        font-size: clamp(2rem, 4vw, 3rem);
        font-weight: 780;
        letter-spacing: -.045em;
        line-height: 1.12;
    }
    .hero-copy { color: #aab7c8; font-size: 1rem; }
    .section-label {
        margin: 1.5rem 0 .6rem;
        color: #96a7ba;
        font-size: .75rem;
        font-weight: 750;
        letter-spacing: .13em;
        text-transform: uppercase;
    }
    [data-testid="stMetric"] {
        min-height: 112px;
        padding: 1.05rem 1.15rem;
        border: 1px solid rgba(148, 163, 184, .16);
        border-radius: 16px;
        background: rgba(19, 31, 48, .82);
    }
    [data-testid="stMetricLabel"] { color: #9badc0; }
    [data-testid="stMetricValue"] { color: #f2f6fb; }
    [data-testid="stForm"] {
        padding: 1.2rem 1.25rem 1rem;
        border: 1px solid rgba(148, 163, 184, .18);
        border-radius: 18px;
        background: rgba(16, 27, 43, .84);
    }
    [data-testid="stTextInput"] input {
        min-height: 3.15rem;
        border: 1px solid rgba(148, 163, 184, .28);
        border-radius: 12px;
        background: #101b2c;
        color: #f2f6fb;
    }
    [data-testid="stTextInput"] input:focus {
        border-color: #55c9b5;
        box-shadow: 0 0 0 .15rem rgba(85, 201, 181, .17);
    }
    [data-testid="stFormSubmitButton"] button {
        min-height: 3.05rem;
        white-space: nowrap;
        border: 0;
        border-radius: 12px;
        background: linear-gradient(100deg, #20a995, #3986c6);
        color: #fff;
        font-weight: 700;
        transition: filter .15s ease, transform .15s ease;
    }
    [data-testid="stFormSubmitButton"] button:hover {
        filter: brightness(1.1);
        transform: translateY(-1px);
    }
    .answer-card {
        margin: .4rem 0 1rem;
        padding: 1.2rem 1.35rem;
        border: 1px solid rgba(82, 211, 183, .3);
        border-left: 4px solid #4cc9ad;
        border-radius: 15px;
        background: linear-gradient(110deg, rgba(20, 54, 58, .76), rgba(19, 37, 53, .84));
    }
    .answer-kicker {
        margin-bottom: .45rem;
        color: #65d7bf;
        font-size: .7rem;
        font-weight: 750;
        letter-spacing: .14em;
    }
    .answer-copy { color: #eef7f5; font-size: 1.06rem; line-height: 1.65; }
    [data-testid="stDataFrame"] {
        overflow: hidden;
        border: 1px solid rgba(148, 163, 184, .2);
        border-radius: 14px;
    }
    [data-testid="stExpander"] {
        border: 1px solid rgba(148, 163, 184, .17);
        border-radius: 13px;
        background: rgba(16, 27, 43, .62);
    }
    @media (max-width: 640px) {
        [data-testid="stMainBlockContainer"] { padding: 1.4rem 1rem 3rem; }
        .hero { padding: 1.35rem; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    f"""
    <div class="hero">
        <div class="hero-kicker">Selected report · {html.escape(selected_report.name)}</div>
        <div class="hero-title">Your placement data, at a glance.</div>
        <div class="hero-copy">
            Ask about recruiters, CTC, offers, or the branches they hired from.
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

max_ctc = pd.to_numeric(df["Highest CTC"], errors="coerce").max()
metric_columns = st.columns(3)
metric_columns[0].metric("Companies in report", f"{len(df):,}")
metric_columns[1].metric("Programs covered", f"{len(PROGRAM_NAMES)}")
metric_columns[2].metric(
    "Highest reported CTC",
    f"{max_ctc:g} LPA" if pd.notna(max_ctc) else "—",
)

st.markdown('<div class="section-label">Ask a question</div>', unsafe_allow_html=True)
st.caption(
    "Try: “Which company hired the most from IT?” · "
    "“Who offered CTC above 20?” · “How many placements at Google?”"
)

with st.form("placement_question_form"):
    question_columns = st.columns([5, 1.4])
    user_q = question_columns[0].text_input(
        "Your question",
        placeholder="Ask something about the placement report...",
        label_visibility="collapsed",
    )
    submitted = question_columns[1].form_submit_button(
        "Find answer",
        width="stretch",
    )

if submitted:
    if not user_q.strip():
        st.warning("Type a question about the placement report first.")
    else:
        with st.spinner("Looking through the placement report..."):
            ctx = retrieve(user_q)
            try:
                answer = generate_answer(ctx, user_q, df)
            except RuntimeError as exc:
                st.error(str(exc))
            else:
                st.markdown(
                    '<div class="section-label">Your answer</div>',
                    unsafe_allow_html=True,
                )
                st.markdown(
                    "<div class='answer-card'>"
                    "<div class='answer-kicker'>PLACEMENT INSIGHT</div>"
                    f"<div class='answer-copy'>{html.escape(answer)}</div>"
                    "</div>",
                    unsafe_allow_html=True,
                )

                result_table = answer_table_for_question(user_q, df)
                if result_table is not None and not result_table.empty:
                    st.dataframe(
                        result_table,
                        hide_index=True,
                        width="stretch",
                    )
