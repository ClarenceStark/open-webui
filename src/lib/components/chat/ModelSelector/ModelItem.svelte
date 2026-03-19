<script lang="ts">
	import { getContext } from 'svelte';

	import Check from '$lib/components/icons/Check.svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	const i18n = getContext('i18n');

	export let selectedModelIdx: number = -1;
	export let item: any = {};
	export let index: number = -1;
	export let value: string = '';

	export let onClick: () => void = () => {};
</script>

<button
	role="option"
	aria-selected={value === item.value}
	aria-label={$i18n.t('Select {{modelName}} model', { modelName: item.label })}
	class="flex group/item w-full text-left select-none items-start rounded-xl py-3 pl-3 pr-2 text-sm text-gray-700 dark:text-gray-100 outline-hidden transition-all duration-75 hover:bg-gray-100 dark:hover:bg-gray-800 cursor-pointer {index ===
	selectedModelIdx
		? 'bg-gray-100 dark:bg-gray-800'
		: ''}"
	data-arrow-selected={index === selectedModelIdx}
	data-value={item.value}
	on:click={() => {
		onClick();
	}}
>
	<div class="flex items-start gap-3 flex-1 min-w-0">
		<div class="flex-1 min-w-0">
			<Tooltip content={item.value} placement="top-start">
				<div class="font-medium line-clamp-1 tracking-[0.02em]">
					{item.label}
				</div>
			</Tooltip>

			{#if item.description}
				<div class="mt-0.5 text-xs leading-5 text-gray-500 dark:text-gray-400 line-clamp-2">
					{item.description}
				</div>
			{/if}
		</div>
	</div>

	<div class="pl-2 pr-1 flex items-center shrink-0 self-center">
		{#if value === item.value}
			<Check className="size-3.5" />
		{/if}
	</div>
</button>
