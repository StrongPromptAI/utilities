#!/usr/bin/env python3
"""Knowledge Base CLI - Fast terminal access to kb_core functions."""

import sys
import click
from pathlib import Path

from scripts.kb_core import (
    semantic_search,
    list_stakeholders,
    get_calls_for_stakeholder,
    get_stakeholder_context,
    suggested_next_step,
    extract_call_quotes,
    get_candidate_quotes,
    get_approved_quotes,
    bulk_approve_quotes,
    bulk_reject_quotes,
    draft_letter,
)


@click.group()
def cli():
    """Knowledge Base CLI for stakeholder intelligence."""
    pass


@cli.command()
@click.argument("query")
@click.option("--stakeholder", "-s", help="Filter by stakeholder name")
@click.option("--project", "-p", help="Filter by project name")
@click.option("--limit", "-l", default=10, help="Max results (default: 10)")
@click.option("--days", "-d", type=int, help="Limit to last N days")
def search(query, stakeholder, project, limit, days):
    """Semantic search across knowledge base."""
    try:
        results = semantic_search(
            query=query,
            stakeholder_name=stakeholder,
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

        if stakeholder:
            click.secho(f"   Stakeholder: {stakeholder}", dim=True)
        if project:
            click.secho(f"   Project: {project}", dim=True)
        if days:
            click.secho(f"   Last {days} days", dim=True)

        click.echo()

        for i, result in enumerate(results, 1):
            # Header
            click.secho(f"[{i}] ", fg="cyan", nl=False)
            click.secho(result['stakeholder_name'], fg="green", bold=True, nl=False)

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

    except Exception as e:
        click.secho(f"Error: {e}", fg="red")
        sys.exit(1)


@cli.command(name="list-stakeholders")
@click.option("--type", "-t", "type_filter", help="Filter by type (client, prospect, partner, etc.)")
def list_stakeholders_cmd(type_filter):
    """List all stakeholders in the knowledge base."""
    try:
        from scripts.kb_core import list_stakeholders as list_stakeholders_fn
        stakeholders = list_stakeholders_fn(type_filter=type_filter)

        if not stakeholders:
            if type_filter:
                click.secho(f"No stakeholders found with type: {type_filter}", fg="yellow")
            else:
                click.secho("No stakeholders found.", fg="yellow")
            return

        click.secho(f"\n📋 Stakeholders", fg="blue", bold=True)
        if type_filter:
            click.secho(f"   Type: {type_filter}", dim=True)
        click.secho(f"   Total: {len(stakeholders)}\n", dim=True)

        for s in stakeholders:
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
@click.option("--stakeholder", "-s", help="Filter by stakeholder name")
def list_calls(stakeholder):
    """List calls, optionally filtered by stakeholder."""
    try:
        if not stakeholder:
            click.secho("Error: --stakeholder is required", fg="red")
            click.secho("Usage: kb list-calls --stakeholder 'Name'", dim=True)
            sys.exit(1)

        calls = get_calls_for_stakeholder(stakeholder)

        if not calls:
            click.secho(f"No calls found for: {stakeholder}", fg="yellow")
            return

        click.secho(f"\n📞 Calls for ", fg="blue", nl=False)
        click.secho(stakeholder, fg="green", bold=True)
        click.secho(f"   Total: {len(calls)}\n", dim=True)

        for call in calls:
            click.secho(f"[{call['id']}] ", fg="cyan", nl=False)
            click.secho(f"{call['call_date']}", fg="yellow", bold=True, nl=False)

            if call.get('project_name'):
                click.secho(f" • {call['project_name']}", fg="blue")
            else:
                click.echo()

            if call.get('participants'):
                participants = ', '.join(call['participants'])
                click.secho(f"     👥 {participants}", dim=True)

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
        click.secho(f"   • Total stakeholder calls: {result['stakeholder_context']['total_calls']}", dim=True)
        click.secho(f"   • Total chunks: {result['stakeholder_context']['total_chunks']}", dim=True)
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
@click.argument("stakeholder_name")
@click.option("--query", "-q", help="Optional semantic search query")
@click.option("--limit", "-l", default=20, help="Max chunks in query results (default: 20)")
def context(stakeholder_name, query, limit):
    """Show comprehensive context about a stakeholder."""
    try:
        result = get_stakeholder_context(stakeholder_name, query=query, limit=limit)

        if 'error' in result:
            click.secho(result['error'], fg="red")
            sys.exit(1)

        # Stakeholder info
        stakeholder = result['stakeholder']
        click.secho(f"\n👤 {stakeholder['name']}", fg="green", bold=True)

        if stakeholder.get('type'):
            click.secho(f"   Type: {stakeholder['type']}", fg="cyan")

        if stakeholder.get('organization'):
            click.secho(f"   Organization: {stakeholder['organization']}", fg="blue")

        if stakeholder.get('notes'):
            click.secho(f"   Notes: {stakeholder['notes']}", dim=True)

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


if __name__ == "__main__":
    cli()
