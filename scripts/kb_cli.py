#!/usr/bin/env python3
"""Knowledge Base CLI - Fast terminal access to kb_core functions."""

import sys
import click
from pathlib import Path


def _flush():
    """Force stdout flush — needed when output is piped to a file (background commands)."""
    sys.stdout.flush()

from scripts.kb_core import (
    semantic_search,
    list_org,
    list_contacts,
    delete_call,
    get_calls_for_org,
    get_org_context,
    get_call_contacts,
    suggested_next_step,
    update_user_notes,
    store_clusters,
    get_cluster_details,
    expand_by_cluster,
    add_call_output,
    get_call_outputs,
)
from scripts.kb_core.summarize import (
    generate_summary,
    get_summary,
    update_summary_content,
)


def _print_llm_banner():
    """Show the active LLM provider/model so the user knows which backend is in use."""
    from scripts.kb_core.config import PRIMARY_LLM_MODEL, PRIMARY_LLM_PROVIDER
    if PRIMARY_LLM_MODEL:
        click.secho(f"LLM: {PRIMARY_LLM_PROVIDER} · {PRIMARY_LLM_MODEL}", fg="blue", dim=True)
    else:
        click.secho("LLM: (not configured — run migration 003)", fg="yellow", dim=True)


@click.group()
def cli():
    """Knowledge Base CLI for client intelligence."""
    pass


@cli.command()
@click.argument("query")
@click.option("--client", "-c", help="Filter by org name")
@click.option("--project", "-p", help="Filter by project name")
@click.option("--limit", "-l", default=10, help="Max results (default: 10)")
@click.option("--days", "-d", type=int, help="Limit to last N days")
@click.option("--expand", "-x", is_flag=True, help="Expand results via cluster membership (agentic search)")
def search(query, client, project, limit, days, expand):
    """Semantic search across knowledge base."""
    try:
        results = semantic_search(
            query=query,
            client_name=client,
            project_name=project,
            limit=limit,
            days_back=days
        )

        if not results:
            click.secho("No results found.", fg="yellow")
            if days:
                click.secho(f"Try expanding beyond {days} days with --days flag or removing it.", dim=True)
            return

        click.secho(f"\n Found {len(results)} results for: ", fg="blue", nl=False)
        click.secho(query, bold=True)

        if client:
            click.secho(f"   Org: {client}", dim=True)
        if project:
            click.secho(f"   Project: {project}", dim=True)
        if days:
            click.secho(f"   Last {days} days", dim=True)

        click.echo()

        for i, result in enumerate(results, 1):
            _display_search_result(i, result)

        # Agentic cluster expansion
        if expand:
            result_ids = [r["id"] for r in results]
            expanded = expand_by_cluster(result_ids)
            if expanded:
                click.secho(f"-- Cluster expansion: {len(expanded)} related chunks --\n", fg="magenta", bold=True)
                for i, ex in enumerate(expanded, len(results) + 1):
                    click.secho(f"[{i}] ", fg="magenta", nl=False)
                    click.secho(ex["client_name"], fg="green", bold=True, nl=False)
                    click.secho(f" * {ex['call_date']}", fg="yellow", nl=False)
                    click.secho(f"  (cluster {ex['cluster_id']})", dim=True)

                    text = ex["text"]
                    if len(text) > 200:
                        text = text[:200] + "..."
                    if ex.get("speaker"):
                        click.secho(f"   {ex['speaker']}: ", fg="magenta", nl=False)
                    else:
                        click.secho("   ", nl=False)
                    click.echo(text)

                    if ex.get("summary"):
                        click.secho(f"   {ex['summary']}", fg="cyan", dim=True)
                    click.echo()
            else:
                click.secho("No cluster expansion available. Run 'kb cluster' first to compute clusters.", dim=True)

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


def _display_search_result(i: int, result: dict):
    """Display a single search result."""
    click.secho(f"[{i}] ", fg="cyan", nl=False)
    click.secho(result['client_name'], fg="green", bold=True, nl=False)

    if result.get('project_name'):
        click.secho(f" * {result['project_name']}", fg="blue", nl=False)

    click.secho(f" * {result['call_date']}", fg="yellow")

    # Scores
    score_info = f"   Score: {result.get('recency_score', 0):.3f}"
    if result.get('days_old') is not None:
        score_info += f" ({result['days_old']} days old)"
    click.secho(score_info, dim=True)

    # Text preview
    text = result['text']
    if len(text) > 200:
        text = text[:200] + "..."

    if result.get('speaker'):
        click.secho(f"   {result['speaker']}: ", fg="magenta", nl=False)
    else:
        click.secho("   ", nl=False)

    click.echo(text)

    # Summary if available
    if result.get('summary'):
        click.secho(f"   {result['summary']}", fg="cyan", dim=True)

    click.echo()


@cli.command(name="list-org")
@click.option("--type", "-t", "type_filter", help="Filter by type (client, end-user, vendor, other)")
def list_org_cmd(type_filter):
    """List all organizations in the knowledge base."""
    try:
        from scripts.kb_core import list_org as list_org_fn
        orgs = list_org_fn(type_filter=type_filter)

        if not orgs:
            if type_filter:
                click.secho(f"No orgs found with type: {type_filter}", fg="yellow")
            else:
                click.secho("No orgs found.", fg="yellow")
            return

        click.secho(f"\nOrganizations", fg="blue", bold=True)
        if type_filter:
            click.secho(f"   Type: {type_filter}", dim=True)
        click.secho(f"   Total: {len(orgs)}\n", dim=True)

        for o in orgs:
            click.secho(f"* {o['name']}", fg="green", bold=True, nl=False)

            if o.get('type'):
                click.secho(f" ({o['type']})", fg="cyan")
            else:
                click.echo()

            if o.get('notes'):
                notes = o['notes']
                if len(notes) > 100:
                    notes = notes[:100] + "..."
                click.secho(f"  {notes}", dim=True)

            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="list-contacts")
@click.option("--org", "-o", "org_name", help="Filter by org name")
def list_contacts_cmd(org_name):
    """List all contacts in the knowledge base."""
    try:
        from scripts.kb_core import list_contacts as list_contacts_fn
        from scripts.kb_core.crud.org import get_org

        org_id = None
        if org_name:
            org = get_org(org_name)
            if not org:
                click.secho(f"Org not found: {org_name}", fg="red")
                sys.exit(1)
            org_id = org["id"]

        contacts = list_contacts_fn(org_id=org_id)

        if not contacts:
            click.secho("No contacts found.", fg="yellow")
            return

        click.secho(f"\nContacts", fg="blue", bold=True)
        if org_name:
            click.secho(f"   Org: {org_name}", dim=True)
        click.secho(f"   Total: {len(contacts)}\n", dim=True)

        for c in contacts:
            click.secho(f"* {c['name']}", fg="green", bold=True, nl=False)

            if c.get('role'):
                click.secho(f" ({c['role']})", fg="cyan", nl=False)

            if c.get('org_name'):
                click.secho(f" @ {c['org_name']}", fg="blue")
            else:
                click.echo()

            if c.get('notes'):
                notes = c['notes']
                if len(notes) > 100:
                    notes = notes[:100] + "..."
                click.secho(f"  {notes}", dim=True)

            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.option("--client", "-c", help="Filter by org name")
def list_calls(client):
    """List calls, optionally filtered by org."""
    try:
        if not client:
            click.secho("Error: --client is required", fg="red")
            click.secho("Usage: kb list-calls --client 'Name'", dim=True)
            sys.exit(1)

        calls = get_calls_for_org(client)

        if not calls:
            click.secho(f"No calls found for: {client}", fg="yellow")
            return

        click.secho(f"\nCalls for ", fg="blue", nl=False)
        click.secho(client, fg="green", bold=True)
        click.secho(f"   Total: {len(calls)}\n", dim=True)

        for call in calls:
            click.secho(f"[{call['id']}] ", fg="cyan", nl=False)
            click.secho(f"{call['call_date']}", fg="yellow", bold=True, nl=False)

            if call.get('project_name'):
                click.secho(f" * {call['project_name']}", fg="blue")
            else:
                click.echo()

            # Show contacts from call_contacts junction
            contacts = get_call_contacts(call['id'])
            if contacts:
                names = ', '.join(c['name'] for c in contacts)
                click.secho(f"     {names}", dim=True)

            if call.get('summary'):
                summary = call['summary']
                if len(summary) > 150:
                    summary = summary[:150] + "..."
                click.secho(f"     {summary}", fg="white")

            for out in get_call_outputs(call['id']):
                label = f" ({out['label']})" if out.get('label') else ""
                click.secho(f"     → {out['path']}{label}", fg="magenta")

            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="add-notes")
@click.argument("call_id", type=int)
@click.argument("notes", required=False)
@click.option("--append", "-a", is_flag=True, help="Append to existing notes instead of replacing")
def add_notes(call_id, notes, append):
    """Add or update personal notes on a call."""
    try:
        from scripts.kb_core.db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT user_notes FROM calls WHERE id = %s", (call_id,))
                row = cur.fetchone()
                if not row:
                    click.secho(f"Call {call_id} not found", fg="red")
                    sys.exit(1)
                existing = row["user_notes"] or ""

        if notes is None:
            if not sys.stdin.isatty():
                click.secho("Notes argument required in non-interactive mode", fg="red")
                click.secho("Usage: kb add-notes <call_id> \"your notes here\"", fg="yellow")
                sys.exit(1)
            if existing:
                click.secho(f"Existing notes for call {call_id}:", fg="blue")
                click.echo(existing)
                click.echo()
            notes = click.edit(existing or "")
            if notes is None:
                click.secho("Aborted (editor closed without saving)", fg="yellow")
                return
            notes = notes.strip()

        if append:
            notes = f"{existing}\n{notes}".strip()

        if update_user_notes(call_id, notes):
            click.secho(f"Notes updated for call {call_id}", fg="green")
        else:
            click.secho(f"Call {call_id} not found", fg="red")
            sys.exit(1)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="link-output")
@click.argument("call_id", type=int)
@click.argument("path", type=click.Path())
@click.option("--label", "-l", help="Human label for the deliverable (e.g. 'JourneyMan role charter')")
def link_output(call_id, path, label):
    """Record an output file (deliverable) a call produced.

    Stores the absolute path so kb is the index — no remembering where the
    comms/ file or email lives. Re-linking the same path updates its label.
    Run once per call when a deliverable derives from several (e.g. two calls).
    """
    try:
        abspath = str(Path(path).expanduser().resolve())
        if not Path(abspath).exists():
            click.secho(f"Warning: {abspath} does not exist (linking anyway)", fg="yellow")
        out_id = add_call_output(call_id, abspath, label)
        click.secho(f"Linked output {out_id} to call {call_id}: ", fg="green", nl=False)
        click.echo(abspath + (f"  ({label})" if label else ""))
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="outputs")
@click.argument("call_id", type=int)
def outputs(call_id):
    """List the output files (deliverables) linked to a call."""
    try:
        rows = get_call_outputs(call_id)
        if not rows:
            click.secho(f"No outputs linked to call {call_id}", fg="yellow")
            return
        click.secho(f"\nOutputs for call {call_id}\n", fg="blue", bold=True)
        for r in rows:
            click.secho(f"[{r['id']}] ", fg="cyan", nl=False)
            click.echo(r["path"], nl=False)
            if r.get("label"):
                click.secho(f"  ({r['label']})", fg="white")
            else:
                click.echo()
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="summary")
@click.argument("call_id", type=int)
@click.option("--phi", is_flag=True, help="Scrub PHI before LLM call; rehydrate output")
@click.option("--max-tokens", type=int, default=8000, help="LLM output cap (default: 8000)")
@click.option("--edit", is_flag=True, help="Open the most-recent stored summary in $EDITOR for in-place editing (UPDATEs the row on save)")
@click.option("--id", "summary_id", type=int, help="With --edit: edit a specific summary id instead of most-recent")
@click.option("--lens", "lens_path", type=click.Path(exists=True), help="Lens file dictating priming context + output contract (e.g. a recap or extraction lens). Default: business-meeting template.")
def summary_cmd(call_id, phi, max_tokens, edit, summary_id, lens_path):
    """Generate or edit a meeting summary.

    Default: generate a comprehensive markdown summary for the call
    (Participants + Decisions/Themes + Action items + Open threads).
    Routes to primary LLM (Opus 4.7) with backup fallback (Gemini 3.5 Flash).

    With --phi: transcript scrubbed before LLM call, output rehydrated before
    storage. Default is non-PHI (most business meetings don't need it).

    With --edit: opens the most-recent stored summary in $EDITOR for direct
    edits. No LLM call. UPDATEs meeting_summaries.content in place on save.
    Use --id to edit a specific summary id.
    """
    import os
    import subprocess
    import tempfile

    try:
        if edit:
            if phi:
                click.secho("Error: --edit and --phi are mutually exclusive (no LLM call in edit mode)", fg="red")
                sys.exit(1)
            summary = get_summary(call_id, summary_id=summary_id)
            if not summary:
                click.secho(f"No summary for call {call_id}.", fg="red")
                click.secho("Run: kb summary <id>  (without --edit) to generate one first.", dim=True)
                sys.exit(1)
            editor = os.environ.get("EDITOR", "vi")
            with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
                f.write(summary["content"])
                tmp_path = f.name
            try:
                subprocess.run([editor, tmp_path], check=True)
                new_content = Path(tmp_path).read_text()
                if new_content == summary["content"]:
                    click.secho("No changes — summary unchanged.", fg="yellow")
                    return
                update_summary_content(summary["id"], new_content)
                click.secho(
                    f"Updated meeting_summaries.id={summary['id']} ({len(new_content)} chars)",
                    fg="green",
                )
            finally:
                os.unlink(tmp_path)
            return

        _print_llm_banner()
        if phi:
            click.secho("PHI mode: transcript will be scrubbed before LLM call", fg="yellow")
        if lens_path:
            click.secho(f"Lens: {Path(lens_path).name}", fg="cyan")
        click.secho(f"Generating summary for call {call_id}...", fg="blue")
        new_id = generate_summary(call_id, phi=phi, max_tokens=max_tokens, lens_path=lens_path)
        summary = get_summary(call_id, summary_id=new_id)
        click.secho(
            f"\nSaved meeting_summaries.id={new_id} "
            f"(model={summary['model_used']}, phi_scrubbed={summary['phi_scrubbed']}, lens={summary.get('lens')})",
            fg="green",
        )
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="show-summary")
@click.argument("call_id", type=int)
@click.option("--id", "summary_id", type=int, help="Specific summary id (default: most recent)")
def show_summary_cmd(call_id, summary_id):
    """Print the stored meeting summary for a call (most recent by default)."""
    try:
        summary = get_summary(call_id, summary_id=summary_id)
        if not summary:
            click.secho(f"No summary for call {call_id}.", fg="yellow")
            click.secho("Run: kb outline <id> && kb summarize <id>", dim=True)
            sys.exit(1)
        click.secho(
            f"# Summary id={summary['id']} · model={summary['model_used']} · "
            f"phi_scrubbed={summary['phi_scrubbed']} · {summary['created_at']:%Y-%m-%d %H:%M}",
            fg="cyan",
        )
        click.echo()
        click.echo(summary["content"])
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="scrub")
@click.argument("input_file", type=click.Path(exists=True))
@click.option("--output", "-o", type=click.Path(), required=True, help="Write scrubbed text here")
@click.option("--mapping", type=click.Path(), help="Write token map (JSON) here for later rehydration")
def scrub_cmd(input_file, output, mapping):
    """Run PHI scrubber (Presidio) over a text file.

    Standalone utility — usable on any text, not just KB transcripts.
    Writes scrubbed text to --output. With --mapping, also writes the
    {token: original} JSON map so you can rehydrate later.

    \b
    Example:
      kb scrub session_notes.txt -o scrubbed.txt --mapping map.json
    """
    import json as _json
    from scripts.kb_core.scrub import scrub

    try:
        text = Path(input_file).read_text()
        scrubbed, token_map = scrub(text)
        Path(output).write_text(scrubbed)
        click.secho(f"Scrubbed {len(text)} chars → {output}", fg="green")
        click.secho(f"  {len(token_map)} entities replaced", dim=True)
        if mapping:
            Path(mapping).write_text(_json.dumps(token_map, indent=2))
            click.secho(f"  Token map → {mapping}", dim=True)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)



@cli.command()
@click.argument("org_name")
@click.option("--query", "-q", help="Optional semantic search query")
@click.option("--limit", "-l", default=20, help="Max chunks in query results (default: 20)")
def context(org_name, query, limit):
    """Show comprehensive context about an org."""
    try:
        result = get_org_context(org_name, query=query, limit=limit)

        if 'error' in result:
            click.secho(result['error'], fg="red")
            sys.exit(1)

        # Org info
        org = result['client']
        click.secho(f"\n{org['name']}", fg="green", bold=True)

        if org.get('type'):
            click.secho(f"   Type: {org['type']}", fg="cyan")

        if org.get('notes'):
            click.secho(f"   Notes: {org['notes']}", dim=True)

        click.echo()

        # Stats
        click.secho(f"Activity:", fg="blue", bold=True)
        click.secho(f"   * Total calls: {len(result['calls'])}", dim=True)
        click.secho(f"   * Total chunks: {result['all_chunks_count']}", dim=True)
        click.echo()

        # Recent calls
        if result['calls']:
            click.secho(f"Recent Calls:", fg="blue", bold=True)
            for call in result['calls'][:5]:
                click.secho(f"   [{call['id']}] ", fg="cyan", nl=False)
                click.secho(f"{call['call_date']}", fg="yellow", nl=False)

                if call.get('project_name'):
                    click.secho(f" * {call['project_name']}", fg="blue")
                else:
                    click.echo()

            if len(result['calls']) > 5:
                click.secho(f"   ... and {len(result['calls']) - 5} more", dim=True)

            click.echo()

        # Query results
        if query and 'relevant_chunks' in result:
            chunks = result['relevant_chunks']
            click.secho(f"Relevant to '{query}':", fg="blue", bold=True)
            click.secho(f"   Found {len(chunks)} chunks\n", dim=True)

            for i, chunk in enumerate(chunks[:5], 1):
                click.secho(f"   [{i}] {chunk['call_date']}", fg="yellow", nl=False)
                if chunk.get('project_name'):
                    click.secho(f" * {chunk['project_name']}", fg="blue")
                else:
                    click.echo()

                text = chunk['text']
                if len(text) > 150:
                    text = text[:150] + "..."
                click.secho(f"       {text}", fg="white")
                click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)











@cli.command()
@click.option("--call", "-c", "call_id", type=int, help="Scope to a single call")
@click.option("--threshold", "-t", default=0.3, help="Distance threshold (0.0-1.0, lower=tighter). Default: 0.3")
@click.option("--min-size", "-m", default=2, help="Minimum cluster size to display (default: 2)")
@click.option("--recompute", is_flag=True, help="Force recomputation of clusters")
def cluster(call_id, threshold, min_size, recompute):
    """Compute and display topic clusters from chunk embeddings.

    Groups semantically related chunks together using agglomerative
    clustering with cosine distance. Use --expand on search to leverage
    clusters for agentic search expansion.
    """
    try:
        # Check if clusters exist, compute if needed
        from scripts.kb_core.db import get_db
        with get_db() as conn:
            with conn.cursor() as cur:
                if call_id:
                    cur.execute(
                        "SELECT count(*) as cnt FROM chunk_clusters WHERE chunk_id IN (SELECT id FROM call_chunks WHERE call_id = %s)",
                        (call_id,),
                    )
                else:
                    cur.execute("SELECT count(*) as cnt FROM chunk_clusters")
                existing = cur.fetchone()["cnt"]

        if existing == 0 or recompute:
            scope = f"call {call_id}" if call_id else "all chunks"
            click.secho(f"Computing clusters for {scope} (threshold={threshold})...", fg="blue")
            result = store_clusters(call_id=call_id, distance_threshold=threshold)
            click.secho(
                f"  {result['clusters']} clusters from {result['chunks_clustered']} chunks\n",
                fg="cyan",
            )
        elif not recompute:
            click.secho(f"Using existing clusters ({existing} assignments). Use --recompute to refresh.\n", dim=True)

        # Display clusters
        clusters = get_cluster_details(call_id=call_id, min_size=min_size)

        if not clusters:
            click.secho("No clusters found.", fg="yellow")
            return

        click.secho(f"Topic Clusters ({len(clusters)} groups):\n", fg="blue", bold=True)

        for cl in clusters:
            # Cluster header
            click.secho(f"== Cluster {cl['cluster_id']} ", fg="cyan", bold=True, nl=False)
            click.secho(f"({cl['size']} chunks) ", fg="cyan", nl=False)

            # Show orgs represented
            orgs = list(set(c["client_name"] for c in cl["chunks"]))
            click.secho(f"[{', '.join(orgs)}]", fg="green")

            # Show representative chunks (first 3)
            for chunk in cl["chunks"][:3]:
                text = chunk["text"]
                if len(text) > 120:
                    text = text[:120] + "..."
                click.secho(f"  * ", fg="white", nl=False)
                if chunk.get("speaker"):
                    click.secho(f"{chunk['speaker']}: ", fg="magenta", nl=False)
                click.echo(text)

            if cl["size"] > 3:
                click.secho(f"  ... and {cl['size'] - 3} more chunks", dim=True)

            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)



@cli.command()
@click.argument("audio_file")
@click.option("--org-id", type=int, help="Organization ID (required for ingest)")
@click.option("--date", "call_date", help="Call date YYYY-MM-DD (required for ingest)")
@click.option("--project-id", type=int, help="Project ID")
@click.option("--contact-ids", help="Comma-separated contact IDs")
@click.option("--summary", help="Brief summary")
@click.option("--source-type", default="podcast", type=click.Choice(["call_transcript", "podcast", "verbal_recap", "research"]))
@click.option("--model", default="mlx-community/whisper-large-v3-turbo", help="MLX Whisper model")
@click.option("--transcript-only", is_flag=True, help="Transcribe only, don't ingest")
def transcribe(audio_file, org_id, call_date, project_id, contact_ids, summary, source_type, model, transcript_only):
    """Transcribe audio and optionally ingest into knowledge base.

    \b
    Examples:
      kb transcribe episode.mp3 --transcript-only
      kb transcribe episode.mp3 --org-id 29 --date 2026-02-13 --project-id 16
    """
    from datetime import date as date_type
    from scripts.kb_core.transcribe import transcribe_audio
    from scripts.kb_ingest import ingest

    try:
        # Step 1: Transcribe
        click.secho(f"Transcribing {audio_file}...", fg="blue")
        result = transcribe_audio(audio_file, model=model)
        click.secho(
            f"  {result['segments']} segments, {result['duration_min']} min, {result['chars']} chars",
            fg="cyan",
        )
        click.secho(f"  JSON: {result['json_path']}", fg="green")

        if transcript_only:
            click.secho("\nDone (transcript only).", fg="green", bold=True)
            return

        # Step 2: Validate ingest args
        if not org_id:
            click.secho("Error: --org-id required for ingest", fg="red")
            sys.exit(1)
        if not call_date:
            click.secho("Error: --date required for ingest", fg="red")
            sys.exit(1)

        # Step 3: Ingest
        click.secho(f"\nIngesting {result['json_path']}...", fg="blue")

        parsed_contacts = None
        if contact_ids:
            parsed_contacts = [int(x.strip()) for x in contact_ids.split(",")]

        ingest_result = ingest(
            file_path=result["json_path"],
            org_id=org_id,
            call_date=date_type.fromisoformat(call_date),
            contact_ids=parsed_contacts,
            source_type=source_type,
            summary=summary,
            project_id=project_id,
        )

        if "error" in ingest_result and ingest_result["error"] != "duplicate":
            click.secho(f"Ingest error: {ingest_result['error']}", fg="red")
            sys.exit(1)

        if ingest_result.get("error") == "duplicate":
            click.secho(f"Already ingested as call {ingest_result['call_id']}", fg="yellow")
        else:
            click.secho(
                f"\nDone: call {ingest_result['call_id']}, "
                f"{ingest_result['chunks_indexed']} chunks indexed",
                fg="green",
                bold=True,
            )

    except FileNotFoundError as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


# --- Ingest: source material analysis (separate from stakeholder pipeline) ---

@click.group("ingest")
def ingest_group():
    """Source material analysis — load, classify, and mine raw transcriptions."""
    pass

cli.add_command(ingest_group)


@ingest_group.command("load")
@click.argument("path")
@click.option("--type", "source_type", required=True, help="Source type: cs_call, interview, etc.")
@click.option("--org", required=True, help="Organization name")
@click.option("--project", required=True, help="Project name")
def ingest_load(path, source_type, org, project):
    """Bulk-load transcription files (.dote or .json) from a directory or single file."""
    import json as json_mod
    import re
    from datetime import date as date_cls
    from scripts.kb_core.crud.org import get_org
    from scripts.kb_core.crud.projects import get_project
    from scripts.kb_core.ingest.crud import (
        create_ingest_source, get_ingest_source_by_file, insert_ingest_chunks,
    )

    org_row = get_org(org)
    if not org_row:
        click.secho(f"Org '{org}' not found. Use kb list-org to see options.", fg="red")
        sys.exit(1)
    project_row = get_project(project)
    if not project_row:
        click.secho(f"Project '{project}' not found.", fg="red")
        sys.exit(1)

    p = Path(path)
    # Support both .json and .dote files
    files = sorted(list(p.glob("*.json")) + list(p.glob("*.dote"))) if p.is_dir() else [p]
    if not files:
        click.secho(f"No .json or .dote files found in {path}", fg="red")
        sys.exit(1)

    click.secho(f"Loading {len(files)} {source_type} files for {org} / {project}", bold=True)

    loaded, skipped, errors = 0, 0, 0
    total_chunks = 0

    for f in files:
        # Dedup check
        existing = get_ingest_source_by_file(str(f))
        if existing:
            skipped += 1
            continue

        try:
            data = json_mod.loads(f.read_text())

            # Detect format: .dote ({lines: [...]}) vs .json ([...])
            if isinstance(data, dict) and "lines" in data:
                # .dote format: {lines: [{speakerDesignation, text, startTime, endTime}]}
                segments = data["lines"]
                is_dote = True
            elif isinstance(data, list):
                segments = data
                is_dote = False
            else:
                segments = [data]
                is_dote = False

            # Parse agent name from filename: [Agent Name]_ext-...
            agent_name = None
            name_match = re.match(r'\[([^\]]+)\]', f.name)
            if name_match:
                agent_name = name_match.group(1)

            # Parse date from filename: ..._YYYYMMDD... pattern
            source_date = None
            date_match = re.search(r'_(\d{4})(\d{2})(\d{2})', f.name)
            if date_match:
                try:
                    source_date = date_cls(
                        int(date_match.group(1)),
                        int(date_match.group(2)),
                        int(date_match.group(3)),
                    )
                except ValueError:
                    pass

            # Build raw_text and chunks
            # .dote: speakerDesignation, text, startTime, endTime
            # .json: text, timestamp, (optional) speaker
            raw_parts = []
            chunks = []
            for seg in segments:
                text = seg.get("text", "").strip()
                if not text:
                    continue

                # Speaker attribution
                speaker = seg.get("speakerDesignation") or seg.get("speaker") or ""
                if speaker:
                    raw_parts.append(f"[{speaker}] {text}")
                else:
                    raw_parts.append(text)

                # Timestamps: .dote uses startTime/endTime ("HH:MM:SS,mmm"), .json uses timestamp ("MM:SS-MM:SS")
                ts_start, ts_end = None, None
                if is_dote:
                    ts_start = seg.get("startTime", "")
                    ts_end = seg.get("endTime", "")
                else:
                    ts = seg.get("timestamp", "")
                    if "-" in ts:
                        parts = ts.split("-", 1)
                        ts_start, ts_end = parts[0].strip(), parts[1].strip()
                chunks.append({"text": text, "timestamp_start": ts_start, "timestamp_end": ts_end})

            raw_text = "\n".join(raw_parts)

            # Create source record
            source_id = create_ingest_source(
                org_id=org_row["id"],
                project_id=project_row["id"],
                source_type=source_type,
                source_file=str(f),
                source_date=source_date,
                agent_name=agent_name,
                raw_text=raw_text,
                segment_count=len(segments),
            )

            # Embed and insert chunks
            count = insert_ingest_chunks(source_id, chunks, show_progress=False)
            total_chunks += count
            loaded += 1
            click.echo(f"  [{loaded}] {f.name}: {count} chunks")

        except Exception as e:
            click.secho(f"  ERROR {f.name}: {e}", fg="red")
            errors += 1

    click.secho(
        f"\nLoaded {loaded} files, {total_chunks} chunks"
        + (f", skipped {skipped} duplicates" if skipped else "")
        + (f", {errors} errors" if errors else ""),
        fg="green" if not errors else "yellow",
        bold=True,
    )


@ingest_group.command("stats")
@click.option("--project", required=True, help="Project name")
@click.option("--type", "source_type", help="Filter by source type")
def ingest_stats(project, source_type):
    """Show category breakdown for ingest sources."""
    from scripts.kb_core.crud.projects import get_project
    from scripts.kb_core.ingest.crud import ingest_stats as get_stats

    project_row = get_project(project)
    if not project_row:
        click.secho(f"Project '{project}' not found.", fg="red")
        sys.exit(1)

    stats = get_stats(project_id=project_row["id"], source_type=source_type)

    click.secho(f"\nIngest Stats ({project})", bold=True)
    click.secho(f"Total sources: {stats['total']}\n")

    if stats["by_type"]:
        click.secho("By source type:", underline=True)
        for row in stats["by_type"]:
            click.echo(f"  {row['source_type']}: {row['cnt']}")
        click.echo()

    if stats["categories"]:
        click.secho("By category:", underline=True)
        in_total, out_total, unclassified = 0, 0, 0
        for row in stats["categories"]:
            cat = row["category"] or "unclassified"
            scope = "IN-SCOPE" if row["in_scope"] else "out"
            if row["in_scope"]:
                in_total += row["cnt"]
            elif row["category"] is None:
                unclassified += row["cnt"]
            else:
                out_total += row["cnt"]
            click.echo(f"  {cat}: {row['cnt']} ({scope})")
        click.echo()
        click.secho(f"In-scope: {in_total} | Out-of-scope: {out_total} | Unclassified: {unclassified}")
    else:
        click.secho("No classifications yet. Run: kb ingest classify --project " + project, dim=True)


@ingest_group.command("list")
@click.option("--project", required=True, help="Project name")
@click.option("--type", "source_type", help="Filter by source type")
@click.option("--scope", type=click.Choice(["in", "out", "unclassified"]), help="Filter by scope")
@click.option("--category", help="Filter by category")
@click.option("--limit", default=50, help="Max results (default: 50)")
def ingest_list(project, source_type, scope, category, limit):
    """List ingest sources with filters."""
    from scripts.kb_core.crud.projects import get_project
    from scripts.kb_core.ingest.crud import list_ingest_sources

    project_row = get_project(project)
    if not project_row:
        click.secho(f"Project '{project}' not found.", fg="red")
        sys.exit(1)

    sources = list_ingest_sources(
        project_id=project_row["id"],
        source_type=source_type,
        scope=scope,
        category=category,
        limit=limit,
    )

    if not sources:
        click.secho("No sources found matching filters.", fg="yellow")
        return

    click.secho(f"\n{'ID':>5}  {'Type':<10} {'Agent':<20} {'Date':<12} {'Category':<12} {'Scope':<6} {'Segs':>4}  Preview", bold=True)
    click.secho("-" * 100)
    for s in sources:
        scope_label = "IN" if s["in_scope"] else ("OUT" if s["in_scope"] is not None else "?")
        cat = s["category"] or "-"
        agent = (s["agent_name"] or "-")[:20]
        date_str = str(s["source_date"]) if s["source_date"] else "-"
        preview = (s["raw_text"] or "")[:50].replace("\n", " ")
        click.echo(f"{s['id']:>5}  {s['source_type']:<10} {agent:<20} {date_str:<12} {cat:<12} {scope_label:<6} {s['segment_count'] or 0:>4}  {preview}")


@ingest_group.command("search")
@click.argument("query")
@click.option("--project", required=True, help="Project name")
@click.option("--type", "source_type", help="Filter by source type")
@click.option("--scope", type=click.Choice(["in", "out"]), help="Filter by scope")
@click.option("--limit", "-l", default=10, help="Max results (default: 10)")
def ingest_search(query, project, source_type, scope, limit):
    """Semantic search across ingest chunks."""
    from scripts.kb_core.crud.projects import get_project
    from scripts.kb_core.embeddings import get_embedding
    from scripts.kb_core.db import get_db

    project_row = get_project(project)
    if not project_row:
        click.secho(f"Project '{project}' not found.", fg="red")
        sys.exit(1)

    query_embedding = get_embedding(query)

    scope_clause = ""
    params: list = [query_embedding, project_row["id"]]
    if scope == "in":
        scope_clause = "AND s.in_scope = true"
    elif scope == "out":
        scope_clause = "AND s.in_scope = false"
    if source_type:
        scope_clause += " AND s.source_type = %s"
        params.append(source_type)

    sql = f"""
        SELECT ic.text, ic.timestamp_start, ic.timestamp_end,
               s.id as source_id, s.agent_name, s.source_date, s.category, s.source_type,
               1 - (ic.embedding <=> %s::vector) as similarity
        FROM ingest_chunks ic
        JOIN ingest_sources s ON ic.ingest_source_id = s.id
        WHERE s.project_id = %s {scope_clause}
        ORDER BY ic.embedding <=> %s::vector
        LIMIT %s
    """
    params.extend([query_embedding, limit])

    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            results = cur.fetchall()

    if not results:
        click.secho("No results found.", fg="yellow")
        return

    for r in results:
        sim = f"{r['similarity']:.3f}"
        ts = f"[{r['timestamp_start']}-{r['timestamp_end']}]" if r["timestamp_start"] else ""
        cat = f" ({r['category']})" if r["category"] else ""
        click.secho(f"\n  [{sim}] source {r['source_id']} — {r['agent_name'] or '?'} {r['source_date'] or ''}{cat} {ts}", fg="cyan")
        click.echo(f"  {r['text'][:200]}")


# ─── Documentation ingest (scraped markdown repos) ─────────────────────────

@cli.group("docs")
def docs_cmd():
    """Ingest + search external documentation (e.g., OpenWebUI docs)."""
    pass


@docs_cmd.command("ingest")
@click.option("--project", "-p", required=True, help="Project name (e.g. openwebui-docs)")
@click.option("--keep", type=click.Path(), default=None,
              help="Keep the cloned repo at this path instead of cleaning up (for debugging)")
@click.option("--limit", type=int, default=None, help="Limit to first N markdown files (dry-run)")
def docs_ingest(project, keep, limit):
    """Clone the docs repo, chunk, embed, upsert. Idempotent."""
    from scripts.kb_core.ingest.docs import ingest_project
    result = ingest_project(project, keep_clone=keep, limit_files=limit)
    click.secho(
        f"\n✅ ingest complete: {result['files']} files · "
        f"{result['chunks_written']} chunks written · "
        f"{result['chunks_pruned']} stale pruned",
        fg="green",
    )


@docs_cmd.command("search")
@click.argument("query")
@click.option("--project", "-p", default="openwebui-docs", help="Project name (default: openwebui-docs)")
@click.option("-k", "--limit", default=5, help="Top K results (default: 5)")
@click.option("--json", "as_json", is_flag=True, help="Output JSON for programmatic consumption")
def docs_search(query, project, limit, as_json):
    """Semantic search over ingested docs. Returns source URL + section + snippet."""
    from scripts.kb_core.crud.docs import semantic_search_docs
    results = semantic_search_docs(query, project_name=project, limit=limit)
    if as_json:
        import json as _json
        click.echo(_json.dumps([{
            "source_url": r["source_url"],
            "section_path": r["section_path"],
            "similarity": round(r["similarity"], 3),
            "text": r["text"],
        } for r in results], indent=2))
        return
    if not results:
        click.secho("(no results — has the project been ingested?)", fg="yellow")
        return
    for i, r in enumerate(results, 1):
        click.secho(f"\n#{i}  sim={r['similarity']:.3f}  {r['section_path'] or '(no section)'}", fg="cyan", bold=True)
        click.echo(f"   {r['source_url']}")
        preview = r["text"][:400] + ("…" if len(r["text"]) > 400 else "")
        click.echo(f"\n{preview}")


@docs_cmd.command("reset")
@click.option("--project", "-p", required=True, help="Project name")
@click.confirmation_option(prompt="Delete all doc_chunks for this project?")
def docs_reset(project):
    """Wipe all doc_chunks for a project. Project row itself is preserved."""
    from scripts.kb_core.crud.docs import semantic_search_docs  # noqa: ensures import works
    from scripts.kb_core.crud.docs import reset_project, get_or_create_project
    pid = get_or_create_project(project)
    n = reset_project(pid)
    click.secho(f"deleted {n} rows", fg="yellow")


@docs_cmd.command("stats")
@click.option("--project", "-p", default="openwebui-docs", help="Project name")
def docs_stats(project):
    """Show count of ingested chunks for a project."""
    from scripts.kb_core.crud.docs import count_chunks
    click.echo(f"{project}: {count_chunks(project)} chunks")


# ─── OpenWebUI runtime state inspection (oxp-kb Postgres) ──────────────────

@cli.group("openwebui")
def openwebui_cmd():
    """Inspect the live OpenWebUI Postgres (users, config, chats, knowledge).

    READ ONLY — this group has no write commands by design.
    For what the docs *say*, use `kb docs search`.
    For what your instance actually *has*, use the commands here.
    """
    pass


@openwebui_cmd.command("stats")
def owu_stats():
    """Row counts across the main OpenWebUI tables."""
    from scripts.kb_core.openwebui_runtime import count_all
    out = count_all()
    for tbl, n in out.items():
        click.echo(f"  {tbl:20} {n}")


@openwebui_cmd.command("users")
@click.option("-l", "--limit", default=20)
def owu_users(limit):
    """List users + their auth.active status."""
    from scripts.kb_core.openwebui_runtime import list_users
    rows = list_users(limit=limit)
    if not rows:
        click.secho("(no users)", fg="yellow"); return
    for r in rows:
        active = "✓" if r["auth_active"] else "✗"
        click.echo(f"  [{active}] {r['email']:40} role={r['role']:8} last_active={r['last_active_at']}")


@openwebui_cmd.command("config")
@click.argument("key", required=False)
@click.option("--json", "as_json", is_flag=True, help="Full JSON dump")
def owu_config(key, as_json):
    """Dump the config singleton (what admin UI saved), or a dotted key.

    Examples:
      kb openwebui config                           # full metadata
      kb openwebui config rag.web.search.engine     # one key
      kb openwebui config --json                    # pretty-print full data
    """
    from scripts.kb_core.openwebui_runtime import get_config, get_config_key
    import json as _json
    if key:
        val = get_config_key(key)
        if val is None:
            click.secho(f"(key '{key}' not set)", fg="yellow")
        else:
            click.echo(_json.dumps(val, indent=2))
        return
    cfg = get_config()
    if as_json:
        click.echo(_json.dumps(cfg, indent=2, default=str))
        return
    if not cfg:
        click.secho("(no config row)", fg="yellow"); return
    click.echo(f"  version:    {cfg.get('version')}")
    click.echo(f"  updated_at: {cfg.get('updated_at')}")
    click.echo(f"  data size:  {len(_json.dumps(cfg.get('data') or {}))} bytes")
    click.echo(f"  top-level keys: {list((cfg.get('data') or {}).keys())}")


@openwebui_cmd.command("knowledge")
@click.option("-l", "--limit", default=20)
def owu_knowledge(limit):
    """List user-uploaded knowledge bases."""
    from scripts.kb_core.openwebui_runtime import list_knowledge
    rows = list_knowledge(limit=limit)
    if not rows:
        click.secho("(no knowledge bases)", fg="yellow"); return
    for r in rows:
        click.echo(f"  {r['name']:40} files={r['file_count']:3}  created={r['created_at']}")


@openwebui_cmd.command("files")
@click.option("-l", "--limit", default=30)
def owu_files(limit):
    """List uploaded files."""
    from scripts.kb_core.openwebui_runtime import list_files
    rows = list_files(limit=limit)
    if not rows:
        click.secho("(no files)", fg="yellow"); return
    for r in rows:
        click.echo(f"  {r['filename']:50} created={r['created_at']}")


@openwebui_cmd.command("chats")
@click.option("-l", "--limit", default=20)
def owu_chats(limit):
    """List chat metadata only (title + message count, not content)."""
    from scripts.kb_core.openwebui_runtime import list_chats
    rows = list_chats(limit=limit)
    if not rows:
        click.secho("(no chats)", fg="yellow"); return
    for r in rows:
        flags = ("📌" if r["pinned"] else "  ") + ("🗄️ " if r["archived"] else "  ")
        click.echo(f"  {flags}  {(r['title'] or '(untitled)')[:50]:50} msgs={r['message_count']:3}  updated={r['updated_at']}")


@openwebui_cmd.command("models")
@click.option("-l", "--limit", default=30)
@click.option("--all", "show_all", is_flag=True, help="Include inactive")
def owu_models(limit, show_all):
    """List configured models."""
    from scripts.kb_core.openwebui_runtime import list_models
    rows = list_models(limit=limit, active_only=not show_all)
    if not rows:
        click.secho("(no models)", fg="yellow"); return
    for r in rows:
        active = "✓" if r["is_active"] else "✗"
        click.echo(f"  [{active}] {r['name']:40} base={r['base_model_id'] or '(none)'}")


@cli.command(name="delete-call")
@click.argument("call_id", type=int)
@click.option("--force", "-f", is_flag=True, help="Skip the confirmation prompt")
def delete_call_cmd(call_id, force):
    """Delete a call and ALL its dependents (chunks, contacts, summaries, content).

    Handles the NO-ACTION `content` FK that a bare DELETE trips over. Shows a
    preview of what will be removed and confirms before deleting (--force skips).
    """
    from scripts.kb_core.db import get_db
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT c.id, c.call_date, c.source_file, o.name AS org_name,
                          (SELECT count(*) FROM call_chunks       WHERE call_id=c.id) AS chunks,
                          (SELECT count(*) FROM content           WHERE call_id=c.id) AS content,
                          (SELECT count(*) FROM call_contacts     WHERE call_id=c.id) AS contacts,
                          (SELECT count(*) FROM meeting_summaries WHERE call_id=c.id) AS summaries
                   FROM calls c JOIN orgs o ON c.org_id=o.id WHERE c.id=%s""",
                (call_id,),
            )
            info = cur.fetchone()
    if not info:
        click.secho(f"Call {call_id} not found.", fg="red"); return
    click.echo(f"Call {info['id']}  {info['call_date']}  org={info['org_name']}")
    click.echo(f"  source: {info['source_file']}")
    click.echo(f"  will delete: {info['chunks']} chunks, {info['content']} content, "
               f"{info['contacts']} contacts, {info['summaries']} summaries")
    if not force and not click.confirm("Delete this call and all the above?", default=False):
        click.secho("Aborted.", fg="yellow"); return
    result = delete_call(call_id)
    if "error" in result:
        click.secho(result["error"], fg="red"); return
    click.secho(
        f"Deleted call {result['deleted_call_id']} "
        f"({result['chunks_deleted']} chunks, {result['content_deleted']} content, "
        f"{result['contacts_deleted']} contacts, {result['summaries_deleted']} summaries).",
        fg="green",
    )


if __name__ == "__main__":
    cli()
