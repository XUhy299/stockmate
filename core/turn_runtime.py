"""Serial turns for CLI, Web and existing file-based message channels."""
import asyncio
from uuid import uuid4

from channels import feishu_channel, wecom_channel
from execution_context import direct_results, progress_sink, scheduled_service, active_session_id, scheduled_execution
from tools.all_tools import context_compressor, subagent_manager
from model_errors import model_error_message


def notify(agent, kind, **data):
    if agent._event_handler:
        agent._event_handler(kind, {**(agent._active_task or {}), **data})


async def deliver(agent, event, content):
    # Routing comes from this event, never from a global "last sender".
    if agent._message_handler and event.metadata.get("source") != "web":
        agent._message_handler(content)
    if event.event_type == "feishu_message":
        await feishu_channel.send_message(
            event.metadata["chat_id"], content, msg_type="interactive"
        )
    elif event.event_type == "wecom_message":
        await wecom_channel.send_message(
            event.metadata["from_user"], content, msg_type="markdown"
        )


async def process_turn(agent, event):
    job_id = event.metadata.get('scheduled_task_id')
    run_id = event.metadata.get('scheduled_run_id')
    if job_id and not agent.scheduled_tasks.begin(job_id, run_id):
        return
    source = event.metadata.get("source", event.event_type)
    agent._active_task = {
        "task_id": event.metadata.get("task_id") or uuid4().hex,
        "source": source, "status": "running", "kind": event.event_type,
    }
    agent._set_status("working")
    agent._last_subagent_content = None
    agent._last_tool_error = None
    sink = progress_sink.set(lambda kind, data: notify(agent, kind, **data))
    direct = direct_results.set(True)
    service_token = scheduled_service.set(agent.scheduled_tasks)
    session_token = active_session_id.set(agent.session.session_id)
    execution_token = scheduled_execution.set(bool(job_id))
    content = None
    try:
        # A source tag is UI metadata; it never enters the model prompt.
        agent.session.add_message(
            "user", event.content, source=source,
            task_id=agent._active_task["task_id"], visible=True
        )
        if not agent.save_session():
            raise RuntimeError("无法保存会话，请检查本地目录权限")
        notify(agent, "started")
        if agent.client is None and source in {"web", "schedule"}:
            raise RuntimeError("请在项目 .env 中配置 OPENAI_API_KEY 后重启")
        if agent.client is None:
            content = await agent._mock_chat(event.content)
        elif event.event_type == "web_daily_brief" or (job_id and event.metadata.get('scheduled_action') == 'daily_brief'):
            notify(agent, "progress", label="正在执行每日简报 Skill")
            result = await subagent_manager.run_with_skill(
                task_name="每日简报",
                task_description="请按 morning_report 技能生成我的自选股每日简报。",
                skill_name="morning_report",
            )
            content = result.get("content", "")
            if not content or content.startswith("Error:"):
                raise RuntimeError(result.get("error") or "每日简报未能生成，请重试")
        else:
            skill = {
                "morning_report": "morning_report",
                "routine_analysis": "research_report",
                "price_alert": "alert_analysis",
            }.get(event.event_type)
            for _ in range(agent.max_work_iterations):
                if await context_compressor.compact_session(agent.session):
                    if not agent.save_session():
                        raise RuntimeError("无法保存上下文摘要，请检查本地目录权限")
                messages = [
                    {"role": "system", "content": agent._build_system_prompt()},
                    *agent._prepare_history_messages(),
                ]
                if skill:
                    messages.append({
                        "role": "system",
                        "content": f"本轮系统任务请使用 spawn_subagent，skill_name='{skill}'。",
                    })
                response = await agent._call_llm(messages)
                choice = response.choices[0]
                if choice.finish_reason == "length":
                    raise RuntimeError("回复达到长度上限，请缩小问题后重试")
                if not choice.message.tool_calls:
                    content = choice.message.content
                    if not content or not content.strip():
                        raise RuntimeError("模型未返回有效内容，请重试")
                    break
                messages, _, spawned = await agent._execute_tool_calls_and_record(
                    choice.message, messages
                )
                if spawned:
                    content = agent._last_subagent_content
                    if not content:
                        raise RuntimeError("子 Agent 未完成报告，请稍后重试")
                    break
                if any(tc.function.name == "idle" for tc in choice.message.tool_calls):
                    if agent._last_tool_error:
                        raise RuntimeError(agent._last_tool_error)
                    content = choice.message.content or "本轮分析已结束。"
                    break
            else:
                raise RuntimeError("已达到分析轮数上限，本次任务未完成")
        agent.session.add_message(
            "assistant", content, source=source,
            task_id=agent._active_task["task_id"], visible=True
        )
        if not agent.save_session():
            raise RuntimeError("结果未能保存，请检查本地目录权限")
        await deliver(agent, event, content)
        notify(agent, "completed", status="completed", content=content)
        if job_id:
            agent.scheduled_tasks.finish(job_id, run_id, 'completed')
    except asyncio.CancelledError:
        agent.save_session()
        notify(agent, "failed", status="failed", error="服务已停止，本次任务未完成")
        if job_id:
            agent.scheduled_tasks.finish(job_id, run_id, 'failed', '服务已停止，本次任务未完成')
        raise
    except Exception as exc:
        agent.logger.exception("Agent turn failed")
        error = str(exc) if isinstance(exc, RuntimeError) else model_error_message(exc, agent.model)
        agent.session.add_message(
            "assistant", error, source=source,
            task_id=agent._active_task["task_id"], visible=True, failed=True,
        )
        agent.save_session()
        try:
            await deliver(agent, event, error)
        except Exception:
            agent.logger.exception("Channel delivery failed")
        finally:
            notify(agent, "failed", status="failed", error=error)
            if job_id:
                agent.scheduled_tasks.finish(job_id, run_id, 'failed', error)
    finally:
        progress_sink.reset(sink)
        direct_results.reset(direct)
        scheduled_service.reset(service_token)
        active_session_id.reset(session_token)
        scheduled_execution.reset(execution_token)
        if agent._pending_web == agent._active_task["task_id"]:
            agent._pending_web = None
        agent._active_task = None
        agent._current_wecom_user = None
        agent._current_feishu_chat_id = None
        agent._current_feishu_chat_type = None
        agent._set_status("idle")


async def run_event_loop(agent):
    agent._set_status("idle")
    try:
        while not agent._shutdown_event.is_set():
            events = agent._drain_mailbox()
            if events:
                await process_turn(agent, events[0])
                continue
            # Legacy worker files have no reliable reply address.
            for result in agent._drain_subagent_results():
                agent.session.add_message("assistant", result["content"], visible=True)
                agent.save_session()
            await asyncio.sleep(0.2)
    finally:
        agent._set_status("shutdown")
