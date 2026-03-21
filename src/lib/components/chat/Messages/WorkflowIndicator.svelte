<script context="module" lang="ts">
	const workflowStartedAtCache = new Map<string, number>();
</script>

<script lang="ts">
	import { getContext } from 'svelte';
	import { onDestroy, onMount } from 'svelte';
	import { slide } from 'svelte/transition';
	import { quintOut } from 'svelte/easing';

	import ChevronUp from '$lib/components/icons/ChevronUp.svelte';
	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';

	const i18n = getContext('i18n');

	export let text: string | null = null;
	export let startedAt: number | string | null = null;
	export let cacheKey: string | null = null;
	export let open = false;
	export let expandable = false;
	export let animated = true;
	export let frozenElapsedSeconds: number | null = null;
	export let showElapsed = true;

	let elapsedSeconds = 0;
	let currentStartedAt = Math.floor(Date.now() / 1000);
	let timer: ReturnType<typeof setInterval> | null = null;
	let resolvedText = '正在思考';
	let resolvedAriaLabel = 'AI 正在思考';

	const nowInSeconds = () => Math.floor(Date.now() / 1000);

	const normalizeStartedAt = (value: number | string | null, key: string | null) => {
		const numericValue =
			typeof value === 'string' && value.trim() !== '' ? Number(value) : value;

		if (!numericValue || !Number.isFinite(numericValue) || numericValue <= 0) {
			if (key && workflowStartedAtCache.has(key)) {
				return workflowStartedAtCache.get(key) ?? nowInSeconds();
			}
			return nowInSeconds();
		}

		const normalized =
			numericValue > 1_000_000_000_000
				? Math.floor(numericValue / 1000)
				: Math.floor(numericValue);

		if (key) {
			const cached = workflowStartedAtCache.get(key);
			const stableStartedAt =
				typeof cached === 'number' && Number.isFinite(cached)
					? Math.min(cached, normalized)
					: normalized;
			workflowStartedAtCache.set(key, stableStartedAt);
			return stableStartedAt;
		}

		return normalized;
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
		if (
			typeof frozenElapsedSeconds === 'number' &&
			Number.isFinite(frozenElapsedSeconds) &&
			frozenElapsedSeconds >= 0
		) {
			elapsedSeconds = Math.floor(frozenElapsedSeconds);
			return;
		}

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
		const nextStartedAt = normalizeStartedAt(startedAt, cacheKey);
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

<div class="workflow-indicator">
	{#if expandable}
		<button
			type="button"
			class="workflow-indicator__button"
			aria-live="polite"
			aria-label={resolvedAriaLabel}
			aria-expanded={open}
			on:click={() => {
				open = !open;
			}}
		>
			<div class="thinking-indicator">
				<span
					class="thinking-indicator__text"
					class:thinking-indicator__text--static={!animated}
					data-text={resolvedText}
					aria-hidden="true"
				>{resolvedText}</span>
				{#if showElapsed}
					<span class="thinking-indicator__divider" aria-hidden="true"></span>
					<span class="thinking-indicator__timer">{formatElapsed(elapsedSeconds)}</span>
				{/if}
			</div>

			<div class="workflow-indicator__chevron" aria-hidden="true">
				{#if open}
					<ChevronUp strokeWidth="3.5" className="size-3.5" />
				{:else}
					<ChevronDown strokeWidth="3.5" className="size-3.5" />
				{/if}
			</div>
		</button>

		{#if open}
			<div class="workflow-indicator__content" transition:slide={{ duration: 300, easing: quintOut, axis: 'y' }}>
				<slot />
			</div>
		{/if}
	{:else}
		<div class="thinking-indicator" aria-live="polite" aria-label={resolvedAriaLabel}>
			<span
				class="thinking-indicator__text"
				class:thinking-indicator__text--static={!animated}
				data-text={resolvedText}
				aria-hidden="true"
			>{resolvedText}</span>
			{#if showElapsed}
				<span class="thinking-indicator__divider" aria-hidden="true"></span>
				<span class="thinking-indicator__timer">{formatElapsed(elapsedSeconds)}</span>
			{/if}
		</div>
	{/if}
</div>

<style>
	.workflow-indicator {
		display: flex;
		flex-direction: column;
		align-items: flex-start;
	}

	.workflow-indicator__button {
		display: inline-flex;
		align-items: center;
		gap: 0.5rem;
		padding: 0;
		border: 0;
		background: transparent;
		color: inherit;
		text-align: left;
	}

	.workflow-indicator__chevron {
		display: inline-flex;
		align-items: center;
		color: #9c9c9c;
		transform: translateY(1px);
	}

	.workflow-indicator__content {
		width: 100%;
		margin-top: 0.35rem;
	}

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

	.thinking-indicator__text--static::after {
		animation: none;
		opacity: 0;
		text-shadow: none;
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
