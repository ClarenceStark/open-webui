import { describe, expect, it } from 'vitest';

import { getAutoChatTitle, sanitizeAssistantDisplayContent } from './index';

describe('sanitizeAssistantDisplayContent', () => {
	it('hides incomplete reasoning details blocks while streaming', () => {
		const content =
			'Answer prefix\n<details type="reasoning" done="false" started_at="1742574472"';

		expect(sanitizeAssistantDisplayContent(content, false)).toBe('Answer prefix\n');
	});

	it('hides partially streamed details tags before the opening tag is complete', () => {
		const content = 'Answer prefix\n<detai';

		expect(sanitizeAssistantDisplayContent(content, false)).toBe('Answer prefix\n');
	});

	it('hides very early partial details prefixes while streaming', () => {
		const content = 'Answer prefix\n<de';

		expect(sanitizeAssistantDisplayContent(content, false)).toBe('Answer prefix\n');
	});

	it('keeps completed reasoning details blocks intact for downstream rendering', () => {
		const content = `<details type="reasoning" done="true" duration="4">
<summary>Thought for 4 seconds</summary>
internal reasoning
</details>
Final answer`;

		expect(sanitizeAssistantDisplayContent(content, true)).toContain(
			'<details type="reasoning" done="true" duration="4">'
		);
	});
});

describe('getAutoChatTitle', () => {
	it('uses the first user message as the initial auto title', () => {
		expect(
			getAutoChatTitle([{ role: 'user', content: '帮我为这个项目的对话标题做一个自动命名机制。' }])
		).toBe('帮我为这个项目的对话标题做一个自动命名机制。');
	});

	it('strips markdown formatting and code blocks', () => {
		expect(
			getAutoChatTitle([
				{
					role: 'user',
					content: '# 标题\n\n请帮我看下这个函数：\n```ts\nconst x = 1;\n```\n然后给我一个方案'
				}
			])
		).toBe('标题 请帮我看下这个函数： 然后给我一个方案');
	});

	it('supports structured content arrays', () => {
		expect(
			getAutoChatTitle([
				{
					role: 'user',
					content: [
						{ type: 'input_text', text: '先分析这个报错' },
						{ type: 'image_url', image_url: 'https://example.com/a.png' },
						{ type: 'text', text: '再给修复方案' }
					]
				}
			])
		).toBe('先分析这个报错 再给修复方案');
	});

	it('falls back when the first user message has no usable text', () => {
		expect(getAutoChatTitle([{ role: 'user', content: '```python\nprint(1)\n```' }], 'New Chat')).toBe(
			'New Chat'
		);
	});

	it('truncates long titles without cutting too aggressively', () => {
		expect(
			getAutoChatTitle(
				[
					{
						role: 'user',
						content:
							'请帮我系统梳理 open-webui 里 Codex 流式消息、工具状态和聊天标题之间的同步关系，并给出最小侵入修复方案'
					}
				],
				'New Chat',
				24
			)
		).toBe('请帮我系统梳理 open-webui 里...');
	});
});
