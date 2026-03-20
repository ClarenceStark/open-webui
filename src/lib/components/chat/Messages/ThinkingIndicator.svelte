<script lang="ts">
	import { getContext } from 'svelte';
	import { onDestroy, onMount } from 'svelte';

	const i18n = getContext('i18n');

	export let text: string | null = null;
	export let startedAt: number | string | null = null;

	let elapsedSeconds = 0;
	let currentStartedAt = Math.floor(Date.now() / 1000);
	let timer: ReturnType<typeof setInterval> | null = null;
	let resolvedText = '正在思考';
	let resolvedAriaLabel = 'AI 正在思考';

	const nowInSeconds = () => Math.floor(Date.now() / 1000);

	const normalizeStartedAt = (value: number | string | null) => {
		const numericValue =
			typeof value === 'string' && value.trim() !== '' ? Number(value) : value;

		if (!numericValue || !Number.isFinite(numericValue) || numericValue <= 0) {
			return nowInSeconds();
		}

		return numericValue > 1_000_000_000_000
			? Math.floor(numericValue / 1000)
			: Math.floor(numericValue);
	};

	const formatElapsed = (seconds: number) => {
		const hours = Math.floor(seconds / 3600);
		const minutes = Math.floor((seconds % 3600) / 60);
		const remainingSeconds = seconds % 60;

		if (hours > 0) {
			return `${hours}:${minutes.toString().padStart(2, '0')}:${remainingSeconds
				.toString()
				.padStart(2, '0')}`;
		}

		return `${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
	};

	const syncElapsed = () => {
		elapsedSeconds = Math.max(0, nowInSeconds() - currentStartedAt);
	};

	const startTimer = () => {
		if (timer) {
			clearInterval(timer);
		}

		syncElapsed();
		timer = setInterval(syncElapsed, 250);
	};

	$: {
		if (text && text.trim().length > 0) {
			resolvedText = text;
		} else {
			const lang = ($i18n?.language ?? '').toLowerCase();
			resolvedText = lang.startsWith('en') ? 'Thinking' : '正在思考';
		}
		resolvedAriaLabel = `AI ${resolvedText}`;
	}

	$: {
		const nextStartedAt = normalizeStartedAt(startedAt);
		if (nextStartedAt !== currentStartedAt) {
			currentStartedAt = nextStartedAt;
			syncElapsed();
		}
	}

	onMount(() => {
		startTimer();
	});

	onDestroy(() => {
		if (timer) {
			clearInterval(timer);
		}
	});
</script>

<div class="thinking-indicator" aria-live="polite" aria-label={resolvedAriaLabel}>
	<span class="thinking-indicator__text" data-text={resolvedText} aria-hidden="true">{resolvedText}</span>
	<span class="thinking-indicator__divider" aria-hidden="true"></span>
	<span class="thinking-indicator__timer">{formatElapsed(elapsedSeconds)}</span>
</div>

<style>
	.thinking-indicator {
		display: inline-flex;
		min-height: 3rem;
		align-items: center;
		gap: 0.55rem;
		animation: thinking-fade-in 0.32s cubic-bezier(0.16, 1, 0.3, 1);
	}

	.thinking-indicator__text {
		position: relative;
		display: inline-block;
		font-size: 1rem;
		font-weight: 400;
		letter-spacing: 0.06em;
		color: #9c9c9c;
	}

	:global(.dark) .thinking-indicator__text {
		color: #9c9c9c;
	}

	.thinking-indicator__text::after {
		content: attr(data-text);
		position: absolute;
		inset: 0;
		color: transparent;
		background: linear-gradient(
			90deg,
			rgba(255, 255, 255, 0) 0%,
			rgba(255, 255, 255, 0) 36%,
			rgba(255, 255, 255, 0.3) 44%,
			rgba(255, 255, 255, 0.95) 50%,
			rgba(255, 255, 255, 0.3) 56%,
			rgba(255, 255, 255, 0) 64%,
			rgba(255, 255, 255, 0) 100%
		);
		background-size: 240% 100%;
		background-repeat: no-repeat;
		-webkit-background-clip: text;
		background-clip: text;
		-webkit-text-fill-color: transparent;
		text-shadow: 0 0 0.45rem rgba(255, 255, 255, 0.14);
		animation: thinking-shimmer 2.6s ease-in-out infinite;
		pointer-events: none;
	}

	.thinking-indicator__divider {
		width: 0.22rem;
		height: 0.22rem;
		flex: 0 0 auto;
		border-radius: 999px;
		background: #9c9c9c;
	}

	:global(.dark) .thinking-indicator__divider {
		background: #9c9c9c;
	}

	.thinking-indicator__timer {
		font-size: 1rem;
		font-variant-numeric: tabular-nums;
		letter-spacing: 0.04em;
		color: #9c9c9c;
	}

	:global(.dark) .thinking-indicator__timer {
		color: #9c9c9c;
	}

	@keyframes thinking-fade-in {
		from {
			opacity: 0;
			transform: translateY(6px);
		}
		to {
			opacity: 1;
			transform: translateY(0);
		}
	}

	@keyframes thinking-shimmer {
		0% {
			background-position: 100% 50%;
		}
		100% {
			background-position: -100% 50%;
		}
	}

	@media (prefers-reduced-motion: reduce) {
		.thinking-indicator,
		.thinking-indicator__text::after {
			animation: none;
		}
	}
</style>
