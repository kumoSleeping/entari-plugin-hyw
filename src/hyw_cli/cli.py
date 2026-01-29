"""
hyw-cli - Command line interface for HYW Core

Usage:
    hyw query "your question"
    hyw agent "your question"
    hyw search "keywords"
    hyw screenshot https://example.com
    hyw fetch https://example.com
    hyw render input.md -o output.png
"""

import asyncio
import json
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

console = Console()


def run_async(coro):
    """Run async function in sync context"""
    return asyncio.run(coro)


def get_core(config_path: Optional[str] = None, **overrides):
    """Initialize HywCore with config"""
    from hyw_core import HywCore, HywCoreConfig

    if config_path:
        config = HywCoreConfig.from_yaml(config_path)
    else:
        config = HywCoreConfig.from_dict(overrides)

    return HywCore(config)


@click.group()
@click.option('--config', '-c', type=click.Path(exists=True), help='Config file path (YAML)')
@click.option('--model', '-m', help='Model name override')
@click.option('--api-key', envvar='OPENAI_API_KEY', help='API key')
@click.option('--base-url', default='https://openrouter.ai/api/v1', help='API base URL')
@click.option('--json-output', '-j', is_flag=True, help='Output as JSON')
@click.pass_context
def cli(ctx, config, model, api_key, base_url, json_output):
    """HYW CLI - LLM pipeline, search and browser automation tool"""
    ctx.ensure_object(dict)
    ctx.obj['config_path'] = config
    ctx.obj['model'] = model
    ctx.obj['api_key'] = api_key
    ctx.obj['base_url'] = base_url
    ctx.obj['json_output'] = json_output


@cli.command()
@click.argument('question')
@click.option('--output', '-o', type=click.Path(), help='Output image path')
@click.pass_context
def query(ctx, question: str, output: Optional[str]):
    """Standard pipeline query"""
    async def _run():
        from hyw_core import QueryRequest

        core = get_core(
            ctx.obj['config_path'],
            model_name=ctx.obj['model'] or '',
            api_key=ctx.obj['api_key'] or '',
            base_url=ctx.obj['base_url'],
        )

        try:
            request = QueryRequest(
                user_input=question,
                conversation_history=[],
            )

            response = await core.query(request, output_path=output)

            if ctx.obj['json_output']:
                click.echo(json.dumps({
                    'success': response.success,
                    'content': response.content,
                    'error': response.error,
                    'usage': response.usage,
                    'cost': response.cost,
                    'total_time': response.total_time,
                }, ensure_ascii=False, indent=2))
            else:
                if response.success:
                    console.print(Markdown(response.content))
                    if output and response.image_path:
                        console.print(f"\n[green]Image saved to: {response.image_path}[/green]")
                else:
                    console.print(f"[red]Error: {response.error}[/red]")
        finally:
            await core.close()

    run_async(_run())


@cli.command()
@click.argument('question')
@click.option('--output', '-o', type=click.Path(), help='Output image path')
@click.pass_context
def agent(ctx, question: str, output: Optional[str]):
    """Agent mode query (autonomous tool calling)"""
    async def _run():
        from hyw_core import QueryRequest

        core = get_core(
            ctx.obj['config_path'],
            model_name=ctx.obj['model'] or '',
            api_key=ctx.obj['api_key'] or '',
            base_url=ctx.obj['base_url'],
        )

        try:
            request = QueryRequest(
                user_input=question,
                conversation_history=[],
            )

            response = await core.query_agent(request, output_path=output)

            if ctx.obj['json_output']:
                click.echo(json.dumps({
                    'success': response.success,
                    'content': response.content,
                    'error': response.error,
                    'references': response.references,
                    'usage': response.usage,
                    'cost': response.cost,
                    'total_time': response.total_time,
                }, ensure_ascii=False, indent=2))
            else:
                if response.success:
                    console.print(Markdown(response.content))

                    if response.references:
                        console.print("\n[bold]References:[/bold]")
                        for i, ref in enumerate(response.references, 1):
                            console.print(f"  [{i}] {ref.get('title', 'N/A')}")
                            console.print(f"      {ref.get('url', '')}")

                    if output and response.image_path:
                        console.print(f"\n[green]Image saved to: {response.image_path}[/green]")
                else:
                    console.print(f"[red]Error: {response.error}[/red]")
        finally:
            await core.close()

    run_async(_run())


@cli.command()
@click.argument('keywords', nargs=-1, required=True)
@click.option('--limit', '-l', default=10, help='Number of results')
@click.option('--engine', '-e', default='duckduckgo', help='Search engine')
@click.pass_context
def search(ctx, keywords: tuple, limit: int, engine: str):
    """Search the web"""
    async def _run():
        core = get_core(
            ctx.obj['config_path'],
            model_name=ctx.obj['model'] or '',
            api_key=ctx.obj['api_key'] or '',
            base_url=ctx.obj['base_url'],
            search_engine=engine,
            search_limit=limit,
        )

        try:
            results = await core.search(list(keywords), engine=engine, limit=limit)

            if ctx.obj['json_output']:
                click.echo(json.dumps(results, ensure_ascii=False, indent=2))
            else:
                for query_idx, query_results in enumerate(results):
                    if len(keywords) > 1:
                        console.print(f"\n[bold]Query: {keywords[query_idx]}[/bold]")

                    table = Table(show_header=True)
                    table.add_column("#", style="dim", width=3)
                    table.add_column("Title", style="cyan")
                    table.add_column("URL", style="green")

                    for i, r in enumerate(query_results, 1):
                        table.add_row(str(i), r.get('title', '')[:50], r.get('url', '')[:60])

                    console.print(table)
        finally:
            await core.close()

    run_async(_run())


@cli.command()
@click.argument('url')
@click.option('--output', '-o', type=click.Path(), help='Output image path')
@click.pass_context
def screenshot(ctx, url: str, output: Optional[str]):
    """Take a screenshot of a webpage"""
    import base64

    async def _run():
        core = get_core(
            ctx.obj['config_path'],
            model_name=ctx.obj['model'] or '',
            api_key=ctx.obj['api_key'] or '',
            base_url=ctx.obj['base_url'],
        )

        try:
            b64_img = await core.screenshot(url)

            if b64_img:
                if output:
                    with open(output, 'wb') as f:
                        f.write(base64.b64decode(b64_img))
                    console.print(f"[green]Screenshot saved to: {output}[/green]")
                elif ctx.obj['json_output']:
                    click.echo(json.dumps({'url': url, 'image_base64': b64_img}))
                else:
                    console.print(f"[green]Screenshot captured ({len(b64_img)} bytes base64)[/green]")
                    console.print("Use -o to save to file or -j for JSON output")
            else:
                console.print(f"[red]Failed to capture screenshot[/red]")
        finally:
            await core.close()

    run_async(_run())


@cli.command()
@click.argument('urls', nargs=-1, required=True)
@click.option('--screenshot', '-s', is_flag=True, help='Include screenshots')
@click.pass_context
def fetch(ctx, urls: tuple, screenshot: bool):
    """Fetch webpage content"""
    async def _run():
        core = get_core(
            ctx.obj['config_path'],
            model_name=ctx.obj['model'] or '',
            api_key=ctx.obj['api_key'] or '',
            base_url=ctx.obj['base_url'],
        )

        try:
            results = await core.fetch_pages(list(urls), include_screenshot=screenshot)

            if ctx.obj['json_output']:
                # Remove screenshot data for cleaner JSON output
                clean_results = []
                for r in results:
                    clean = {k: v for k, v in r.items() if k != 'screenshot'}
                    if screenshot and 'screenshot' in r:
                        clean['has_screenshot'] = bool(r['screenshot'])
                    clean_results.append(clean)
                click.echo(json.dumps(clean_results, ensure_ascii=False, indent=2))
            else:
                for r in results:
                    console.print(Panel(
                        f"[bold]{r.get('title', 'No title')}[/bold]\n"
                        f"URL: {r.get('url', '')}\n\n"
                        f"{r.get('content', '')[:500]}...",
                        title=r.get('url', '')[:50]
                    ))
        finally:
            await core.close()

    run_async(_run())


@cli.command()
@click.argument('input_file', type=click.Path())
@click.option('--output', '-o', type=click.Path(), required=True, help='Output image path')
@click.pass_context
def render(ctx, input_file: str, output: str):
    """Render Markdown to image"""
    async def _run():
        core = get_core(
            ctx.obj['config_path'],
            model_name=ctx.obj['model'] or '',
            api_key=ctx.obj['api_key'] or '',
            base_url=ctx.obj['base_url'],
        )

        try:
            # Read from stdin if input is '-'
            if input_file == '-':
                content = sys.stdin.read()
            else:
                with open(input_file, 'r', encoding='utf-8') as f:
                    content = f.read()

            success = await core.render(content, output)

            if success:
                console.print(f"[green]Rendered to: {output}[/green]")
            else:
                console.print(f"[red]Render failed[/red]")
        finally:
            await core.close()

    run_async(_run())


if __name__ == '__main__':
    cli()
