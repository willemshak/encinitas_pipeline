#!/usr/bin/env python3
"""Simple local dashboard to browse permit pipeline data."""

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
import config

try:
    from flask import Flask, render_template_string
except ImportError:
    print("Flask not installed. Install with: pip install flask")
    sys.exit(1)

app = Flask(__name__)

BASE_TEMPLATE = """
<!DOCTYPE html>
<html>
<head>
    <title>Encinitas Permit Pipeline</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
               background: #f5f5f5; color: #333; max-width: 1200px; margin: 0 auto; padding: 20px; }
        h1 { margin-bottom: 20px; color: #1a1a1a; }
        h2 { margin: 20px 0 10px; color: #333; }
        nav { background: #2c3e50; padding: 12px 20px; margin: -20px -20px 20px; }
        nav a { color: #ecf0f1; text-decoration: none; margin-right: 20px; font-weight: 500; }
        nav a:hover { color: #3498db; }
        .card { background: white; border-radius: 8px; padding: 16px; margin-bottom: 12px;
                box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .card h3 { margin-bottom: 8px; }
        .badge { display: inline-block; padding: 2px 8px; border-radius: 12px;
                 font-size: 12px; font-weight: 600; margin-right: 6px; }
        .badge-dept { background: #3498db; color: white; }
        .badge-count { background: #2ecc71; color: white; }
        .badge-type { background: #9b59b6; color: white; }
        table { width: 100%; border-collapse: collapse; margin: 10px 0; }
        th, td { padding: 8px 12px; text-align: left; border-bottom: 1px solid #eee; }
        th { background: #f8f9fa; font-weight: 600; }
        tr:hover { background: #f0f7ff; }
        a { color: #2980b9; }
        .desc { color: #666; font-size: 14px; margin-top: 4px; }
        .stat { font-size: 24px; font-weight: 700; color: #2c3e50; }
        .stat-label { font-size: 12px; color: #999; text-transform: uppercase; }
        .stats-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 12px; margin-bottom: 20px; }
        .condition { background: #fff3cd; padding: 4px 8px; border-radius: 4px; font-size: 13px; margin-top: 4px; }
        pre { background: #f8f9fa; padding: 12px; border-radius: 4px; overflow-x: auto; font-size: 13px; }
    </style>
</head>
<body>
    <nav>
        <a href="/">Index</a>
        <a href="/graph">Graph</a>
        <a href="/log">Pipeline Log</a>
    </nav>
    {% block content %}{% endblock %}
</body>
</html>
"""

INDEX_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
    <h1>Encinitas Permit Pipeline</h1>
    <div class="stats-grid">
        <div class="card"><div class="stat">{{ total }}</div><div class="stat-label">Total Permits</div></div>
        <div class="card"><div class="stat">{{ departments|length }}</div><div class="stat-label">Departments</div></div>
        <div class="card"><div class="stat">{{ structured_count }}</div><div class="stat-label">Extracted</div></div>
    </div>
    {% for dept, permits in departments.items() %}
    <div class="card">
        <h3><span class="badge badge-dept">{{ dept }}</span> <span class="badge badge-count">{{ permits|length }}</span></h3>
        <table>
            <tr><th>Permit</th><th>Description</th><th>Data</th></tr>
            {% for p in permits %}
            <tr>
                <td><a href="/permit/{{ p.slug }}">{{ p.permit_name }}</a></td>
                <td class="desc">{{ p.description[:100] }}{% if p.description|length > 100 %}...{% endif %}</td>
                <td>{% if p.has_structured %}<span class="badge badge-count">extracted</span>{% endif %}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endfor %}
""")

PERMIT_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
    <h1>{{ data.permit_name }}</h1>
    <p class="desc"><span class="badge badge-dept">{{ data.department }}</span></p>
    <div class="card">
        <h3>Summary</h3>
        <p>{{ data.plain_english_summary or data.description or 'No summary available' }}</p>
    </div>

    {% if data.review_routing %}
    <div class="card">
        <h3>Review Routing</h3>
        {% if data.review_routing.divisions %}<p><strong>Divisions:</strong> {{ data.review_routing.divisions|join(', ') }}</p>{% endif %}
        {% if data.review_routing.timeline %}<p><strong>Timeline:</strong> {{ data.review_routing.timeline }}</p>{% endif %}
        {% if data.review_routing.submission_method %}<p><strong>Submit via:</strong> {{ data.review_routing.submission_method }}</p>{% endif %}
    </div>
    {% endif %}

    {% if data.always_required %}
    <div class="card">
        <h3>Always Required</h3>
        <table>
            <tr><th>Document</th><th>Type</th><th>Description</th></tr>
            {% for doc in data.always_required %}
            <tr>
                <td>{% if doc.url %}<a href="{{ doc.url }}">{{ doc.name }}</a>{% else %}{{ doc.name }}{% endif %}</td>
                <td><span class="badge badge-type">{{ doc.document_type }}</span></td>
                <td class="desc">{{ doc.description }}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    {% if data.conditionally_required %}
    <div class="card">
        <h3>Conditionally Required</h3>
        {% for doc in data.conditionally_required %}
        <div style="margin-bottom: 12px;">
            <strong>{{ doc.name }}</strong>
            <div class="condition">IF: {{ doc.condition_text }}</div>
            <div class="desc">{{ doc.description }}</div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {% if data.process_steps %}
    <div class="card">
        <h3>Process Steps</h3>
        <ol>
        {% for step in data.process_steps %}
            <li>{{ step.description }}</li>
        {% endfor %}
        </ol>
    </div>
    {% endif %}

    {% if data.related_permits %}
    <div class="card">
        <h3>Related Permits</h3>
        <table>
            <tr><th>Permit</th><th>Relationship</th><th>Condition</th></tr>
            {% for rel in data.related_permits %}
            <tr><td>{{ rel.name }}</td><td>{{ rel.relationship }}</td><td>{{ rel.condition_text or '' }}</td></tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}

    <div class="card">
        <h3>Raw JSON</h3>
        <pre>{{ raw_json }}</pre>
    </div>
""")

GRAPH_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
    <h1>Knowledge Graph</h1>
    <div class="stats-grid">
        <div class="card"><div class="stat">{{ stats.total_nodes }}</div><div class="stat-label">Nodes</div></div>
        <div class="card"><div class="stat">{{ stats.total_edges }}</div><div class="stat-label">Edges</div></div>
        <div class="card"><div class="stat">{{ stats.orphan_nodes|length }}</div><div class="stat-label">Orphans</div></div>
    </div>
    <div class="card">
        <h3>Nodes by Type</h3>
        <table><tr><th>Type</th><th>Count</th></tr>
        {% for t, c in stats.nodes_by_type.items() %}<tr><td>{{ t }}</td><td>{{ c }}</td></tr>{% endfor %}
        </table>
    </div>
    <div class="card">
        <h3>Edges by Type</h3>
        <table><tr><th>Type</th><th>Count</th></tr>
        {% for t, c in stats.edges_by_type.items() %}<tr><td>{{ t }}</td><td>{{ c }}</td></tr>{% endfor %}
        </table>
    </div>
    {% if stats.most_connected_nodes %}
    <div class="card">
        <h3>Most Connected Nodes</h3>
        <table><tr><th>Node</th><th>Edges</th></tr>
        {% for n in stats.most_connected_nodes %}<tr><td>{{ n.id }}</td><td>{{ n.edge_count }}</td></tr>{% endfor %}
        </table>
    </div>
    {% endif %}
    {% if stats.shared_facts_detected %}
    <div class="card">
        <h3>Shared Facts</h3>
        {% for sf in stats.shared_facts_detected %}
        <p><strong>{{ sf.fact_type }}</strong>: {{ sf.appears_on_permits|join(', ') }}</p>
        {% endfor %}
    </div>
    {% endif %}
""")

LOG_TEMPLATE = BASE_TEMPLATE.replace("{% block content %}{% endblock %}", """
    <h1>Pipeline Run Log</h1>
    {% if crawl_log %}
    <div class="card">
        <h3>Index Crawl</h3>
        <p>Status: {{ crawl_log.status_code }} | Size: {{ crawl_log.page_size_bytes }} bytes | Permits: {{ crawl_log.total_permits }}</p>
        <p>Time: {{ crawl_log.timestamp }}</p>
    </div>
    {% endif %}
    {% if raw_log %}
    <div class="card">
        <h3>Page Crawls ({{ raw_log|length }} entries)</h3>
        <table>
            <tr><th>Slug</th><th>Status</th><th>Size</th><th>Links</th><th>PDFs</th><th>Time</th></tr>
            {% for e in raw_log %}
            <tr>
                <td>{{ e.slug }}</td>
                <td>{{ e.status_code or 'skip' }}</td>
                <td>{{ e.page_size }}</td>
                <td>{{ e.links_found_count }}</td>
                <td>{{ e.pdfs_downloaded }}{% if e.pdfs_failed %} ({{ e.pdfs_failed }} failed){% endif %}</td>
                <td>{{ e.fetch_time_ms }}ms</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}
    {% if extraction_log %}
    <div class="card">
        <h3>LLM Extractions ({{ extraction_log|length }} entries)</h3>
        <table>
            <tr><th>Slug</th><th>Input Tokens</th><th>Output Tokens</th><th>Cost</th><th>Status</th></tr>
            {% for e in extraction_log %}
            <tr>
                <td>{{ e.slug }}</td>
                <td>{{ e.input_tokens }}</td>
                <td>{{ e.output_tokens }}</td>
                <td>${{ '%.4f'|format(e.cost_estimate) }}</td>
                <td>{% if e.success %}OK{% elif e.skipped %}skipped{% else %}{{ e.error }}{% endif %}</td>
            </tr>
            {% endfor %}
        </table>
    </div>
    {% endif %}
""")


def load_json(path: Path):
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return None


@app.route("/")
def index():
    permits = load_json(config.INDEX_DIR / "permits_index.json") or []
    structured_slugs = {p.stem for p in config.STRUCTURED_DIR.glob("*.json") if not p.name.startswith("_")}

    departments = {}
    for p in permits:
        p["has_structured"] = p["slug"] in structured_slugs
        departments.setdefault(p["department"], []).append(p)

    return render_template_string(INDEX_TEMPLATE,
        total=len(permits),
        departments=departments,
        structured_count=len(structured_slugs),
    )


@app.route("/permit/<slug>")
def permit_detail(slug):
    path = config.STRUCTURED_DIR / f"{slug}.json"
    if path.exists():
        with open(path) as f:
            data = json.load(f)
    else:
        # Fall back to index data
        permits = load_json(config.INDEX_DIR / "permits_index.json") or []
        data = next((p for p in permits if p["slug"] == slug), {"permit_name": slug, "slug": slug})

    return render_template_string(PERMIT_TEMPLATE,
        data=data,
        raw_json=json.dumps(data, indent=2),
    )


@app.route("/graph")
def graph():
    stats = load_json(config.GRAPH_DIR / "graph_stats.json") or {
        "total_nodes": 0, "total_edges": 0, "orphan_nodes": [],
        "nodes_by_type": {}, "edges_by_type": {},
        "most_connected_nodes": [], "shared_facts_detected": [],
    }
    return render_template_string(GRAPH_TEMPLATE, stats=stats)


@app.route("/log")
def log_page():
    return render_template_string(LOG_TEMPLATE,
        crawl_log=load_json(config.INDEX_DIR / "crawl_log.json"),
        raw_log=load_json(config.RAW_DIR / "_crawl_log.json") or [],
        extraction_log=load_json(config.STRUCTURED_DIR / "_extraction_log.json") or [],
    )


def main():
    port = 8050
    print(f"Starting dashboard at http://localhost:{port}")
    app.run(host="127.0.0.1", port=port, debug=True)


if __name__ == "__main__":
    main()
