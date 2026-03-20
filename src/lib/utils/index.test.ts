import { describe, expect, it } from 'vitest';

import { sanitizeAssistantDisplayContent } from './index';

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
