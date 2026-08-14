"""CLI entry point for InSAR Agent"""

import argparse
import os
import sys
from pathlib import Path


def cmd_config(args):
    from insar_agent.config import load_settings, save_settings
    settings = load_settings()
    if args.api_key:
        settings['deepseek_api_key'] = args.api_key
    if args.base_url:
        settings['deepseek_base_url'] = args.base_url
    if args.model:
        settings['deepseek_model'] = args.model
    save_settings(settings)
    print(f'Configuration saved.')
    print(f'  API Key: {"***" if settings.get("deepseek_api_key") else "not set"}')
    print(f'  Base URL: {settings.get("deepseek_base_url")}')
    print(f'  Model: {settings.get("deepseek_model")}')


def cmd_chat(args):
    from insar_agent.agent import InsarAgent

    def _print_event(evt: dict):
        etype = evt.get('type', '')
        if etype == 'thinking':
            print(f'💭 {evt["text"]}')
        elif etype == 'tool_start':
            print(f'🔧 调用工具: {evt["tool_name"]}')
        elif etype == 'tool_result':
            print(f'✅ 工具完成: {evt["tool_name"]}')
        elif etype == 'tool_progress':
            print(f'   {evt["text"]}')
        elif etype == 'agent_response':
            print(evt['text'])
        elif etype == 'error':
            print(f'❌ 错误: {evt["message"]}')

    agent = InsarAgent(callback=_print_event)
    response, _ = agent.chat(args.message)
    print(response)


def cmd_workflow(args):
    if args.command_name == 'run':
        from insar_agent.workflow import WorkflowRunner

        params = {
            'vector': args.vector,
            'lon': args.lon,
            'lat': args.lat,
            'start': args.start,
            'end': args.end,
            'flight_dir': args.flight_dir,
            'polarization': args.polarization,
            'path': args.path,
            'frame': args.frame,
            'max_neighbors': args.neighbors,
            'looks': args.looks,
        }
        params = {k: v for k, v in params.items() if v is not None}

        def log_cb(msg):
            print(msg)

        runner = WorkflowRunner(args.name, callback=log_cb)
        runner.set_params(**params)
        results = runner.run_all(start_from=args.start_from)
        print(f'\nPipeline results:')
        for step, result in results.items():
            status = result.get('status', 'unknown')
            print(f'  {step}: {status}')
            if status == 'failed':
                print(f'    Error: {result.get("error")}')

    elif args.command_name == 'status':
        from insar_agent.workflow import WorkflowRunner
        runner = WorkflowRunner(args.name)
        import json
        print(json.dumps(runner.status(), indent=2, ensure_ascii=False))


def cmd_query(args):
    from insar_agent.tools.query_slc import query_slc
    result = query_slc(
        vector=args.vector,
        lon=args.lon or 117.2,
        lat=args.lat or 35.6,
        start=args.start or '2021-01-01',
        end=args.end or '2023-12-31',
        flight_dir=args.flight_dir,
        output_dir=args.output or '.',
    )
    print(f'Total scenes: {result.total_scenes}')
    print(f'Stacks found: {len(result.stacks)}')
    for s in result.stacks:
        print(f'  Path {s.path} Frame {s.frame}: {s.count} scenes ({s.normal_count} normal, {s.anomaly_count} anomaly)')
        print(f'    Date range: {s.start_date} ~ {s.end_date}')
    if result.warnings:
        for w in result.warnings:
            print(f'  Warning: {w}')


def cmd_status(args):
    from insar_agent.tools.check_status import check_job_status
    result = check_job_status(job_file=args.job_file)
    print(f'Total: {result.total}  Succeeded: {result.succeeded}  Running: {result.running}  Pending: {result.pending}  Failed: {result.failed}')
    print(f'All done: {result.all_done}')
    if result.url_file:
        print(f'URL file: {result.url_file}')


def cmd_account(args):
    from insar_agent.config import load_accounts, save_accounts
    if args.command_name == 'list':
        accounts = load_accounts()
        if not accounts:
            print('No accounts configured.')
            return
        from hyp3_sdk import HyP3
        for acc in accounts:
            try:
                h = HyP3(username=acc['username'], password=acc['password'])
                creds = h.check_credits()
                print(f'{acc["username"]}: {creds} credits')
            except Exception as e:
                print(f'{acc["username"]}: Error - {e}')
    elif args.command_name == 'add':
        accounts = load_accounts()
        username = args.username or input('ASF Username: ')
        password = args.password or input('ASF Password: ')
        accounts.append({'username': username, 'password': password})
        save_accounts(accounts)
        print(f'Account {username} added.')
    elif args.command_name == 'remove':
        accounts = load_accounts()
        username = args.username
        accounts = [a for a in accounts if a['username'] != username]
        save_accounts(accounts)
        print(f'Account {username} removed.')


def cmd_submit(args):
    from insar_agent.tools.submit_insar import submit_insar_jobs
    from insar_agent.tools.query_slc import query_slc
    from insar_agent.config import load_accounts
    accounts = load_accounts()

    if args.stack_index is not None and args.path is None:
        result = query_slc(
            vector=args.vector,
            start=args.start, end=args.end,
            flight_dir=args.flight_dir,
        )
        if not result.stacks:
            print('No stacks found.')
            return
        if args.stack_index >= len(result.stacks):
            print(f'Invalid stack index. Available: 0-{len(result.stacks)-1}')
            return
        stack = result.stacks[args.stack_index]
        args.path = stack.path
        args.frame = stack.frame
        print(f'Selected: Path {args.path} Frame {args.frame} ({stack.count} scenes)')

    if args.path is None or args.frame is None:
        print('Please specify --path and --frame, or --stack-index')
        return

    result = submit_insar_jobs(
        path=args.path, frame=args.frame,
        project_name=args.name or f'Path{args.path}_Frame{args.frame}',
        max_neighbors=args.neighbors,
        insar_opts={'looks': args.looks},
        accounts=accounts if accounts else None,
        vector=args.vector,
        start=args.start, end=args.end,
        flight_dir=args.flight_dir,
        polarization=args.polarization,
        output_dir=args.output or '.',
    )
    print(f'Project: {result.project_name}')
    print(f'Pairs: {result.total_pairs}  Success: {result.success_count}  Failed: {result.failed_count}')
    print(f'Cost: ~{result.estimated_cost} credits')
    print(f'Job file: {result.job_file}')
    if result.warnings:
        for w in result.warnings:
            print(f'Warning: {w}')


def main():
    parser = argparse.ArgumentParser(description='InSAR Agent - NL-driven InSAR processing')
    sub = parser.add_subparsers(dest='cmd')

    p_chat = sub.add_parser('chat', help='Chat with the InSAR agent (DeepSeek LLM)')
    p_chat.add_argument('message', nargs='?', default='', help='Your message/instruction')

    p_cfg = sub.add_parser('config', help='Configure settings')
    p_cfg_act = p_cfg.add_subparsers(dest='command_name')
    p_cfg_set = p_cfg_act.add_parser('set')
    p_cfg_set.add_argument('--api-key', help='DeepSeek API key')
    p_cfg_set.add_argument('--base-url', help='API base URL')
    p_cfg_set.add_argument('--model', help='Model name')

    p_wf = sub.add_parser('workflow', help='Manage processing workflows')
    p_wf_act = p_wf.add_subparsers(dest='command_name')
    p_wf_run = p_wf_act.add_parser('run', help='Run a workflow')
    p_wf_run.add_argument('name', help='Project name')
    p_wf_run.add_argument('--vector', help='AOI shapefile path')
    p_wf_run.add_argument('--lon', type=float, help='Center longitude')
    p_wf_run.add_argument('--lat', type=float, help='Center latitude')
    p_wf_run.add_argument('--start', help='Start date (YYYY-MM-DD)')
    p_wf_run.add_argument('--end', help='End date (YYYY-MM-DD)')
    p_wf_run.add_argument('--flight-dir', choices=['ASCENDING', 'DESCENDING'])
    p_wf_run.add_argument('--polarization', choices=['VV', 'VH', 'VV+VH'])
    p_wf_run.add_argument('--path', type=int, help='Path number')
    p_wf_run.add_argument('--frame', type=int, help='Frame number')
    p_wf_run.add_argument('--neighbors', type=int, default=2, help='SBAS neighbors (default 2)')
    p_wf_run.add_argument('--looks', default='20x4', choices=['20x4', '10x2'])
    p_wf_run.add_argument('--start-from', type=int, default=0, help='Start from step index (0-5)')
    p_wf_st = p_wf_act.add_parser('status', help='Show workflow status')
    p_wf_st.add_argument('name', help='Project name')

    p_q = sub.add_parser('query', help='Quick SLC data query')
    p_q.add_argument('--vector', help='AOI shapefile path')
    p_q.add_argument('--lon', type=float)
    p_q.add_argument('--lat', type=float)
    p_q.add_argument('--start')
    p_q.add_argument('--end')
    p_q.add_argument('--flight-dir', choices=['ASCENDING', 'DESCENDING'])
    p_q.add_argument('--output', '-o', help='Output directory')

    p_s = sub.add_parser('status', help='Check HyP3 job status')
    p_s.add_argument('job_file', help='Path to job ID file')

    p_acc = sub.add_parser('account', help='Manage ASF accounts')
    p_acc_act = p_acc.add_subparsers(dest='command_name')
    p_acc_list = p_acc_act.add_parser('list', help='List accounts and credits')
    p_acc_add = p_acc_act.add_parser('add', help='Add an account')
    p_acc_add.add_argument('--username', '-u')
    p_acc_add.add_argument('--password', '-p')
    p_acc_rm = p_acc_act.add_parser('remove', help='Remove an account')
    p_acc_rm.add_argument('username')

    p_sub = sub.add_parser('submit', help='Submit InSAR jobs directly')
    p_sub.add_argument('--name', help='Project name')
    p_sub.add_argument('--vector', help='AOI shapefile')
    p_sub.add_argument('--start', help='Start date')
    p_sub.add_argument('--end', help='End date')
    p_sub.add_argument('--flight-dir', choices=['ASCENDING', 'DESCENDING'])
    p_sub.add_argument('--polarization', choices=['VV', 'VH', 'VV+VH'])
    p_sub.add_argument('--stack-index', type=int, help='Auto-select stack by index')
    p_sub.add_argument('--path', type=int, help='Path number')
    p_sub.add_argument('--frame', type=int, help='Frame number')
    p_sub.add_argument('--neighbors', type=int, default=2)
    p_sub.add_argument('--looks', default='20x4', choices=['20x4', '10x2'])
    p_sub.add_argument('--output', '-o', help='Output directory')

    args = parser.parse_args()

    if args.cmd == 'chat':
        if args.message:
            cmd_chat(args)
        else:
            from insar_agent.agent import InsarAgent
            print('InSAR Agent - Chat Mode (DeepSeek LLM)')
            print('Type your instructions in Chinese, or /quit to exit.')
            print('Examples:')
            print('  > 查询成都地区2024年全年的升轨SLC数据')
            print('  > 对Path 89 Frame 106提交InSAR，20x4视数，3邻域配对')
            print('  > 检查所有job状态')
            print()
            agent = InsarAgent()
            history = None
            try:
                import readline
            except ImportError:
                pass
            while True:
                try:
                    msg = input('> ').strip()
                except (EOFError, KeyboardInterrupt):
                    print('\nGoodbye!')
                    break
                if msg.lower() in ('/quit', '/exit', '/q'):
                    print('Goodbye!')
                    break
                if not msg:
                    continue
                response, history = agent.chat(msg, history=history)
                print(response)
                print()
    elif args.cmd == 'config':
        cmd_config(args)
    elif args.cmd == 'workflow':
        cmd_workflow(args)
    elif args.cmd == 'query':
        cmd_query(args)
    elif args.cmd == 'status':
        cmd_status(args)
    elif args.cmd == 'account':
        cmd_account(args)
    elif args.cmd == 'submit':
        cmd_submit(args)
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
