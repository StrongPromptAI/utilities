#!/usr/bin/env python3
"""Knowledge Base CLI - Fast terminal access to kb_core functions."""

import sys
import click
from pathlib import Path

from scripts.kb_core import (
    semantic_search,
    list_clients,
    get_calls_for_client,
    get_client_context,
    get_call_participants,
    suggested_next_step,
    extract_call_quotes,
    get_candidate_quotes,
    get_approved_quotes,
    bulk_approve_quotes,
    bulk_reject_quotes,
    draft_letter,
    harvest_call,
    list_decisions,
    get_candidate_decisions,
    get_decision,
    confirm_decision,
    reject_decision,
    update_decision_status,
    list_open_questions,
    get_candidate_questions,
    resolve_question,
    abandon_question,
    store_clusters,
    get_cluster_details,
    expand_by_cluster,
)


@click.group()
def cli():
    """Knowledge Base CLI for client intelligence."""
    pass


@cli.command()
@click.argument("query")
@click.option("--client", "-c", help="Filter by client name")
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

        click.secho(f"\n🔍 Found {len(results)} results for: ", fg="blue", nl=False)
        click.secho(query, bold=True)

        if client:
            click.secho(f"   Client: {client}", dim=True)
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
                click.secho(f"── Cluster expansion: {len(expanded)} related chunks ──\n", fg="magenta", bold=True)
                for i, ex in enumerate(expanded, len(results) + 1):
                    click.secho(f"[{i}] ", fg="magenta", nl=False)
                    click.secho(ex["client_name"], fg="green", bold=True, nl=False)
                    click.secho(f" • {ex['call_date']}", fg="yellow", nl=False)
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
                        click.secho(f"   📝 {ex['summary']}", fg="cyan", dim=True)
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
        click.secho(f" • {result['project_name']}", fg="blue", nl=False)

    click.secho(f" • {result['call_date']}", fg="yellow")

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
        click.secho(f"   📝 {result['summary']}", fg="cyan", dim=True)

    click.echo()


@cli.command(name="list-clients")
@click.option("--type", "-t", "type_filter", help="Filter by type (client, prospect, partner, etc.)")
def list_clients_cmd(type_filter):
    """List all clients in the knowledge base."""
    try:
        from scripts.kb_core import list_clients as list_clients_fn
        clients = list_clients_fn(type_filter=type_filter)

        if not clients:
            if type_filter:
                click.secho(f"No clients found with type: {type_filter}", fg="yellow")
            else:
                click.secho("No clients found.", fg="yellow")
            return

        click.secho(f"\n📋 Clients", fg="blue", bold=True)
        if type_filter:
            click.secho(f"   Type: {type_filter}", dim=True)
        click.secho(f"   Total: {len(clients)}\n", dim=True)

        for s in clients:
            click.secho(f"• {s['name']}", fg="green", bold=True, nl=False)

            if s.get('type'):
                click.secho(f" ({s['type']})", fg="cyan", nl=False)

            if s.get('organization'):
                click.secho(f" @ {s['organization']}", fg="blue")
            else:
                click.echo()

            if s.get('notes'):
                notes = s['notes']
                if len(notes) > 100:
                    notes = notes[:100] + "..."
                click.secho(f"  {notes}", dim=True)

            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.option("--client", "-c", help="Filter by client name")
def list_calls(client):
    """List calls, optionally filtered by client."""
    try:
        if not client:
            click.secho("Error: --client is required", fg="red")
            click.secho("Usage: kb list-calls --client 'Name'", dim=True)
            sys.exit(1)

        calls = get_calls_for_client(client)

        if not calls:
            click.secho(f"No calls found for: {client}", fg="yellow")
            return

        click.secho(f"\n📞 Calls for ", fg="blue", nl=False)
        click.secho(client, fg="green", bold=True)
        click.secho(f"   Total: {len(calls)}\n", dim=True)

        for call in calls:
            click.secho(f"[{call['id']}] ", fg="cyan", nl=False)
            click.secho(f"{call['call_date']}", fg="yellow", bold=True, nl=False)

            if call.get('project_name'):
                click.secho(f" • {call['project_name']}", fg="blue")
            else:
                click.echo()

            # Show participants from participants table
            participants = get_call_participants(call['id'])
            if participants:
                names = ', '.join(p['name'] for p in participants)
                click.secho(f"     👥 {names}", dim=True)

            if call.get('summary'):
                summary = call['summary']
                if len(summary) > 150:
                    summary = summary[:150] + "..."
                click.secho(f"     {summary}", fg="white")

            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.argument("call_id", type=int)
@click.option("--letter", "-l", help="Path to letter/email to review")
def analyze(call_id, letter):
    """Generate agentic analysis prompt for a call."""
    try:
        click.secho(f"\n🔍 Analyzing call {call_id}...\n", fg="blue")

        result = suggested_next_step(call_id, letter_path=letter)

        # Display the formatted prompt
        click.secho("=" * 80, fg="cyan")
        click.echo(result['analysis_prompt'])
        click.secho("=" * 80, fg="cyan")

        # Additional context
        click.echo()
        click.secho("📊 Analysis Context:", fg="blue", bold=True)
        click.secho(f"   • Agentic search results: {len(result['agentic_search_results'])}", dim=True)
        click.secho(f"   • Total client calls: {result['client_context']['total_calls']}", dim=True)
        click.secho(f"   • Total chunks: {result['client_context']['total_chunks']}", dim=True)
        click.secho(f"   • Approved quotes: {len(result.get('quotes', []))}", dim=True)
        if result.get('letter'):
            click.secho(f"   • Letter included: Yes", dim=True)
        click.echo()

    except ValueError as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)
    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.argument("client_name")
@click.option("--query", "-q", help="Optional semantic search query")
@click.option("--limit", "-l", default=20, help="Max chunks in query results (default: 20)")
def context(client_name, query, limit):
    """Show comprehensive context about a client."""
    try:
        result = get_client_context(client_name, query=query, limit=limit)

        if 'error' in result:
            click.secho(result['error'], fg="red")
            sys.exit(1)

        # Client info
        client = result['client']
        click.secho(f"\n👤 {client['name']}", fg="green", bold=True)

        if client.get('type'):
            click.secho(f"   Type: {client['type']}", fg="cyan")

        if client.get('organization'):
            click.secho(f"   Organization: {client['organization']}", fg="blue")

        if client.get('notes'):
            click.secho(f"   Notes: {client['notes']}", dim=True)

        click.echo()

        # Stats
        click.secho(f"📊 Activity:", fg="blue", bold=True)
        click.secho(f"   • Total calls: {len(result['calls'])}", dim=True)
        click.secho(f"   • Total chunks: {result['all_chunks_count']}", dim=True)
        click.echo()

        # Recent calls
        if result['calls']:
            click.secho(f"📞 Recent Calls:", fg="blue", bold=True)
            for call in result['calls'][:5]:
                click.secho(f"   [{call['id']}] ", fg="cyan", nl=False)
                click.secho(f"{call['call_date']}", fg="yellow", nl=False)

                if call.get('project_name'):
                    click.secho(f" • {call['project_name']}", fg="blue")
                else:
                    click.echo()

            if len(result['calls']) > 5:
                click.secho(f"   ... and {len(result['calls']) - 5} more", dim=True)

            click.echo()

        # Query results
        if query and 'relevant_chunks' in result:
            chunks = result['relevant_chunks']
            click.secho(f"🔍 Relevant to '{query}':", fg="blue", bold=True)
            click.secho(f"   Found {len(chunks)} chunks\n", dim=True)

            for i, chunk in enumerate(chunks[:5], 1):
                click.secho(f"   [{i}] {chunk['call_date']}", fg="yellow", nl=False)
                if chunk.get('project_name'):
                    click.secho(f" • {chunk['project_name']}", fg="blue")
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


@cli.command(name="pick-quotes")
@click.argument("call_id", type=int)
@click.option("--review", is_flag=True, help="Review existing candidates only (skip extraction)")
@click.option("--show", is_flag=True, help="Show approved quotes only")
def pick_quotes(call_id, review, show):
    """Extract and curate notable quotes from a call."""
    try:
        # Show mode: display approved quotes
        if show:
            quotes = get_approved_quotes(call_id)
            if not quotes:
                click.secho(f"No approved quotes for call {call_id}", fg="yellow")
                return

            click.secho(f"\n✓ Approved quotes for call {call_id}:", fg="green", bold=True)
            click.echo()
            for q in quotes:
                _display_quote(q, show_id=False)
            return

        # Extract new candidates (unless --review)
        if not review:
            click.secho(f"\nExtracting quotes from call {call_id}...", fg="blue")
            result = extract_call_quotes(call_id)

            if "error" in result:
                click.secho(result["error"], fg="red")
                sys.exit(1)

            click.secho(
                f"\n  Processed {result['batches_processed']} batches, "
                f"found {result['quotes_extracted']} candidate quotes\n",
                fg="cyan"
            )

        # Get candidates for review
        candidates = get_candidate_quotes(call_id)
        if not candidates:
            click.secho("No candidate quotes to review.", fg="yellow")

            # Check for approved quotes
            approved = get_approved_quotes(call_id)
            if approved:
                click.secho(f"  ({len(approved)} quotes already approved)", dim=True)
            return

        click.secho(f"Found {len(candidates)} candidate quotes:\n", fg="blue", bold=True)

        # Build ID mapping for user input
        id_map = {}
        for idx, q in enumerate(candidates, 1):
            id_map[idx] = q["id"]
            _display_quote(q, display_num=idx)

        # Interactive approval loop
        click.echo()
        click.secho("Actions:", fg="cyan", bold=True)
        click.secho('  Enter numbers to approve (e.g., "1 3 5")', dim=True)
        click.secho('  "all" - approve all', dim=True)
        click.secho('  "none" - reject all', dim=True)
        click.secho('  "done" - finish and save', dim=True)
        click.secho('  "quit" - exit without saving', dim=True)
        click.echo()

        approved_ids = []
        rejected_ids = []
        pending_ids = [q["id"] for q in candidates]

        while pending_ids:
            action = click.prompt(">", default="done").strip().lower()

            if action == "quit":
                click.secho("Exited without saving.", fg="yellow")
                return

            if action == "done":
                break

            if action == "all":
                approved_ids.extend(pending_ids)
                pending_ids = []
                click.secho(f"Approved all {len(approved_ids)} quotes", fg="green")
                break

            if action == "none":
                rejected_ids.extend(pending_ids)
                pending_ids = []
                click.secho("Rejected all quotes", fg="yellow")
                break

            # Parse numbers
            try:
                nums = [int(n) for n in action.split()]
                for n in nums:
                    if n in id_map and id_map[n] in pending_ids:
                        approved_ids.append(id_map[n])
                        pending_ids.remove(id_map[n])
                        click.secho(f"  Approved [{n}]", fg="green")
                    elif n not in id_map:
                        click.secho(f"  Invalid number: {n}", fg="red")
            except ValueError:
                click.secho("  Enter numbers separated by spaces, or 'all'/'none'/'done'/'quit'", fg="red")

            if pending_ids:
                remaining = [k for k, v in id_map.items() if v in pending_ids]
                click.secho(f"  Remaining: {remaining}", dim=True)

        # Save approvals/rejections
        if approved_ids:
            count = bulk_approve_quotes(approved_ids)
            click.secho(f"\n✓ Approved {count} quotes", fg="green", bold=True)

        # Reject remaining pending
        remaining_to_reject = [pid for pid in pending_ids if pid not in approved_ids]
        if remaining_to_reject:
            bulk_reject_quotes(remaining_to_reject)

        if rejected_ids:
            bulk_reject_quotes(rejected_ids)
            click.secho(f"  Rejected {len(rejected_ids)} quotes", dim=True)

        # Show final count
        final_approved = get_approved_quotes(call_id)
        click.secho(f"\nTotal approved quotes for call {call_id}: {len(final_approved)}", fg="cyan")

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


def _display_quote(quote: dict, display_num: int = None, show_id: bool = True):
    """Display a single quote in formatted output."""
    if display_num:
        click.secho(f"[{display_num}] ", fg="cyan", nl=False)

    click.secho(f'"{quote["quote_text"]}"', fg="white", bold=True)

    meta = []
    if quote.get("speaker"):
        meta.append(f"Speaker: {quote['speaker']}")
    if quote.get("category"):
        meta.append(f"Category: {quote['category']}")

    if meta:
        click.secho(f"    {' | '.join(meta)}", fg="magenta")

    if quote.get("context"):
        click.secho(f"    Context: {quote['context']}", dim=True)

    click.echo()


@cli.command(name="draft-letter")
@click.argument("call_id", type=int)
@click.option("--instructions", "-i", help="Custom instructions for the letter")
@click.option("--no-quotes", is_flag=True, help="Exclude approved quotes from letter")
@click.option("--output", "-o", help="Output file path (default: ~/Desktop/letter-name-date.md)")
@click.option("--stdout", is_flag=True, help="Print to stdout instead of file")
def draft_letter_cmd(call_id, instructions, no_quotes, output, stdout):
    """Generate a markdown follow-up letter for a call."""
    try:
        click.secho(f"Drafting letter for call {call_id}...", fg="blue")

        result = draft_letter(
            call_id=call_id,
            instructions=instructions,
            include_quotes=not no_quotes
        )

        if "error" in result:
            click.secho(result["error"], fg="red")
            sys.exit(1)

        markdown = result["markdown"]

        if stdout:
            click.echo(markdown)
        else:
            # Determine output path
            if output:
                out_path = Path(output).expanduser()
            else:
                # Default to symlink_docs/comms/ in current project
                comms_dir = Path("symlink_docs/comms")
                if not comms_dir.exists():
                    comms_dir.mkdir(parents=True, exist_ok=True)
                out_path = comms_dir / result["filename"]

            out_path.write_text(markdown)
            click.secho(f"\n✓ Letter saved to: {out_path}", fg="green", bold=True)
            click.secho(f"  Recipient: {result['recipient']}", dim=True)
            click.secho(f"  Call date: {result['call_date']}", dim=True)

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.argument("call_id", type=int)
@click.option("--project", "-p", required=True, help="Project name")
@click.option("--review", is_flag=True, help="Review existing candidates only (skip extraction)")
def harvest(call_id, project, review):
    """Extract decisions and open questions from a call."""
    try:
        from scripts.kb_core.crud.projects import get_project

        proj = get_project(project)
        if not proj:
            click.secho(f"Project not found: {project}", fg="red")
            click.secho("Available projects:", dim=True)
            from scripts.kb_core import list_projects
            for p in list_projects():
                click.secho(f"  • {p['name']}", dim=True)
            sys.exit(1)

        project_id = proj["id"]

        # Extract (unless --review)
        if not review:
            click.secho(f"\nHarvesting from call {call_id} for project '{project}'...", fg="blue")
            result = harvest_call(call_id, project_id)

            if "error" in result:
                click.secho(result["error"], fg="red")
                sys.exit(1)

            click.secho(
                f"\n  Found {result['decisions_extracted']} decisions, "
                f"{result['questions_extracted']} open questions\n",
                fg="cyan",
            )

        # Display candidates for review
        decisions = get_candidate_decisions(project_id, call_id)
        questions = get_candidate_questions(project_id, call_id)

        if not decisions and not questions:
            click.secho("No candidates to review.", fg="yellow")
            return

        # Display decisions
        if decisions:
            click.secho("DECISIONS:", fg="blue", bold=True)
            click.echo()
            d_map = {}
            for idx, d in enumerate(decisions, 1):
                d_map[idx] = d["id"]
                status_color = "green" if d["status"] == "confirmed" else "yellow"
                click.secho(f"[{idx}] ", fg="cyan", nl=False)
                click.secho(f"({d['status']}) ", fg=status_color, nl=False)
                click.secho(d["topic"], fg="white", bold=True)
                click.secho(f"    {d['summary']}", fg="white")
                if d.get("decided_by"):
                    click.secho(f"    Decided by: {', '.join(d['decided_by'])}", dim=True)
                click.echo()

        # Display questions
        if questions:
            q_offset = len(decisions)
            click.secho("OPEN QUESTIONS:", fg="blue", bold=True)
            click.echo()
            q_map = {}
            for idx, q in enumerate(questions, q_offset + 1):
                q_map[idx] = q["id"]
                click.secho(f"[{idx}] ", fg="cyan", nl=False)
                click.secho(q["topic"], fg="white", bold=True)
                click.secho(f"    {q['question']}", fg="white")
                if q.get("context"):
                    click.secho(f"    Why: {q['context']}", dim=True)
                if q.get("owner"):
                    click.secho(f"    Owner: {q['owner']}", fg="magenta")
                click.echo()

        # Interactive approval
        click.secho("Actions:", fg="cyan", bold=True)
        click.secho('  Enter numbers to approve (e.g., "1 3 5")', dim=True)
        click.secho('  "all" - approve all', dim=True)
        click.secho('  "none" - reject all', dim=True)
        click.secho('  "done" - finish (approve selected, reject rest)', dim=True)
        click.secho('  "quit" - exit without saving', dim=True)
        click.echo()

        approved_d_ids = []
        approved_q_ids = []
        all_d_ids = [d["id"] for d in decisions]
        all_q_ids = [q["id"] for q in questions]

        while True:
            action = click.prompt(">", default="done").strip().lower()

            if action == "quit":
                click.secho("Exited without saving.", fg="yellow")
                return

            if action == "done":
                break

            if action == "all":
                approved_d_ids = all_d_ids[:]
                approved_q_ids = all_q_ids[:]
                click.secho(f"Approved all ({len(approved_d_ids)} decisions, {len(approved_q_ids)} questions)", fg="green")
                break

            if action == "none":
                click.secho("Rejected all candidates", fg="yellow")
                break

            try:
                nums = [int(n) for n in action.split()]
                for n in nums:
                    if n in d_map and d_map[n] not in approved_d_ids:
                        approved_d_ids.append(d_map[n])
                        click.secho(f"  Approved decision [{n}]", fg="green")
                    elif n in q_map and q_map[n] not in approved_q_ids:
                        approved_q_ids.append(q_map[n])
                        click.secho(f"  Approved question [{n}]", fg="green")
                    elif n not in d_map and n not in q_map:
                        click.secho(f"  Invalid number: {n}", fg="red")
            except ValueError:
                click.secho("  Enter numbers, or 'all'/'none'/'done'/'quit'", fg="red")

        # Apply approvals/rejections
        for did in approved_d_ids:
            confirm_decision(did)
        for did in all_d_ids:
            if did not in approved_d_ids:
                reject_decision(did)

        for qid in approved_q_ids:
            pass  # open questions stay open (approved = keep)
        for qid in all_q_ids:
            if qid not in approved_q_ids:
                abandon_question(qid)

        click.secho(f"\nConfirmed {len(approved_d_ids)} decisions, kept {len(approved_q_ids)} questions", fg="green", bold=True)

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.argument("project")
@click.option("--status", "-s", help="Filter: open, confirmed, superseded")
def decisions(project, status):
    """List decisions for a project."""
    try:
        from scripts.kb_core.crud.projects import get_project

        proj = get_project(project)
        if not proj:
            click.secho(f"Project not found: {project}", fg="red")
            sys.exit(1)

        items = list_decisions(proj["id"], status=status)

        if not items:
            click.secho(f"No decisions found for '{project}'", fg="yellow")
            if status:
                click.secho(f"  (filtered by status: {status})", dim=True)
            return

        click.secho(f"\nDecisions for '{project}':", fg="blue", bold=True)
        if status:
            click.secho(f"  Status: {status}", dim=True)
        click.echo()

        status_colors = {"open": "yellow", "confirmed": "green", "superseded": "red"}

        for d in items:
            color = status_colors.get(d["status"], "white")
            click.secho(f"[{d['id']}] ", fg="cyan", nl=False)
            click.secho(f"({d['status']}) ", fg=color, nl=False)
            click.secho(d["topic"], fg="white", bold=True)
            click.secho(f"    {d['summary']}", fg="white")
            if d.get("decided_by"):
                click.secho(f"    Decided by: {', '.join(d['decided_by'])}", dim=True)
            if d.get("source_call_ids"):
                click.secho(f"    Source calls: {d['source_call_ids']}", dim=True)
            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.argument("project")
@click.option("--status", "-s", default="open", help="Filter: open, answered, abandoned")
def questions(project, status):
    """List open questions for a project."""
    try:
        from scripts.kb_core.crud.projects import get_project

        proj = get_project(project)
        if not proj:
            click.secho(f"Project not found: {project}", fg="red")
            sys.exit(1)

        items = list_open_questions(proj["id"], status=status)

        if not items:
            click.secho(f"No questions found for '{project}'", fg="yellow")
            if status:
                click.secho(f"  (filtered by status: {status})", dim=True)
            return

        click.secho(f"\nOpen questions for '{project}':", fg="blue", bold=True)
        if status:
            click.secho(f"  Status: {status}", dim=True)
        click.echo()

        status_colors = {"open": "yellow", "answered": "green", "abandoned": "red"}

        for q in items:
            color = status_colors.get(q["status"], "white")
            click.secho(f"[{q['id']}] ", fg="cyan", nl=False)
            click.secho(f"({q['status']}) ", fg=color, nl=False)
            click.secho(q["topic"], fg="white", bold=True)
            click.secho(f"    {q['question']}", fg="white")
            if q.get("context"):
                click.secho(f"    Why: {q['context']}", dim=True)
            if q.get("owner"):
                click.secho(f"    Owner: {q['owner']}", fg="magenta")
            if q.get("resolution"):
                click.secho(f"    Resolution: {q['resolution']}", fg="green")
            if q.get("source_call_id"):
                click.secho(f"    Source call: {q['source_call_id']}", dim=True)
            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command()
@click.argument("question_id", type=int)
@click.option("--resolution", "-r", required=True, help="The answer/resolution")
@click.option("--decision", "-d", type=int, help="Link to decision ID")
def resolve(question_id, resolution, decision):
    """Mark an open question as answered."""
    try:
        from scripts.kb_core.crud.open_questions import get_open_question

        q = get_open_question(question_id)
        if not q:
            click.secho(f"Question {question_id} not found", fg="red")
            sys.exit(1)

        if q["status"] != "open":
            click.secho(f"Question {question_id} is already '{q['status']}'", fg="yellow")
            return

        resolve_question(question_id, resolution, decision_id=decision)
        click.secho(f"Resolved question {question_id}: {q['topic']}", fg="green", bold=True)
        click.secho(f"  Resolution: {resolution}", dim=True)
        if decision:
            click.secho(f"  Linked to decision: {decision}", dim=True)

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="update-decision")
@click.argument("decision_id", type=int)
@click.option("--status", "-s", type=click.Choice(["open", "confirmed", "superseded"]), help="New status")
@click.option("--summary", "-m", help="Updated summary text")
def update_decision_cmd(decision_id, status, summary):
    """Update a decision's status and/or summary."""
    try:
        d = get_decision(decision_id)
        if not d:
            click.secho(f"Decision {decision_id} not found", fg="red")
            sys.exit(1)

        if not status and not summary:
            click.secho(f"[{d['id']}] ({d['status']}) {d['topic']}", fg="cyan", bold=True)
            click.secho(f"    {d['summary']}", fg="white")
            if d.get("decided_by"):
                click.secho(f"    Decided by: {', '.join(d['decided_by'])}", dim=True)
            if d.get("source_call_ids"):
                click.secho(f"    Source calls: {d['source_call_ids']}", dim=True)
            click.echo()
            click.secho("Use --status and/or --summary to update", dim=True)
            return

        new_status = status or d["status"]
        update_decision_status(decision_id, new_status, summary=summary)

        click.secho(f"Updated decision {decision_id}: {d['topic']}", fg="green", bold=True)
        if status:
            click.secho(f"  Status: {d['status']} → {status}", dim=True)
        if summary:
            preview = summary[:120] + "..." if len(summary) > 120 else summary
            click.secho(f"  Summary: {preview}", dim=True)

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="dismiss-question")
@click.argument("question_id", type=int)
@click.option("--reason", "-r", help="Why this question is being dismissed")
def dismiss_question_cmd(question_id, reason):
    """Dismiss an open question (out of scope, not applicable, etc.)."""
    try:
        from scripts.kb_core.crud.open_questions import get_open_question

        q = get_open_question(question_id)
        if not q:
            click.secho(f"Question {question_id} not found", fg="red")
            sys.exit(1)

        if q["status"] != "open":
            click.secho(f"Question {question_id} is already '{q['status']}'", fg="yellow")
            return

        if reason:
            resolve_question(question_id, reason)
            click.secho(f"Dismissed question {question_id}: {q['topic']}", fg="green", bold=True)
            click.secho(f"  Reason: {reason}", dim=True)
        else:
            abandon_question(question_id)
            click.secho(f"Abandoned question {question_id}: {q['topic']}", fg="yellow", bold=True)

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
                        "SELECT count(*) as cnt FROM chunk_clusters WHERE chunk_id IN (SELECT id FROM chunks WHERE call_id = %s)",
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
            click.secho(f"━━ Cluster {cl['cluster_id']} ", fg="cyan", bold=True, nl=False)
            click.secho(f"({cl['size']} chunks) ", fg="cyan", nl=False)

            # Show clients represented
            clients = list(set(c["client_name"] for c in cl["chunks"]))
            click.secho(f"[{', '.join(clients)}]", fg="green")

            # Show representative chunks (first 3)
            for chunk in cl["chunks"][:3]:
                text = chunk["text"]
                if len(text) > 120:
                    text = text[:120] + "..."
                click.secho(f"  • ", fg="white", nl=False)
                if chunk.get("speaker"):
                    click.secho(f"{chunk['speaker']}: ", fg="magenta", nl=False)
                click.echo(text)

            if cl["size"] > 3:
                click.secho(f"  ... and {cl['size'] - 3} more chunks", dim=True)

            click.echo()

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


if __name__ == "__main__":
    cli()
