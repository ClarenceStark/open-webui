<script lang="ts">
	import { DropdownMenu } from 'bits-ui';
	import { getContext, tick } from 'svelte';

	import { mobile, settings, config, models, type Model } from '$lib/stores';
	import { getModels } from '$lib/apis';
	import { flyAndScale } from '$lib/utils/transitions';

	import ChevronDown from '$lib/components/icons/ChevronDown.svelte';
	import ModelItem from './ModelItem.svelte';

	const i18n = getContext('i18n');

	export let id = '';
	export let value = '';
	export let placeholder = $i18n.t('Select a model');
	export let searchEnabled = false;
	export let searchPlaceholder = $i18n.t('Search a model');

	export let items: {
		label: string;
		value: string;
		description?: string;
		model: Model;
		// eslint-disable-next-line @typescript-eslint/no-explicit-any
		[key: string]: any;
	}[] = [];

	export let className = 'w-[32rem]';
	export let triggerClassName = 'text-lg';

	export let pinModelHandler: (modelId: string) => void = () => {};

	let show = false;
	let selectedModel = '';
	let selectedModelIdx = 0;
	let listScrollTop = 0;
	let listContainer;

	const ITEM_HEIGHT = 64;
	const OVERSCAN = 10;

	$: selectedModel = items.find((item) => item.value === value) ?? '';
	$: filteredItems = items.filter((item) => !(item.model?.info?.meta?.hidden ?? false));
	$: visibleStart = Math.max(0, Math.floor(listScrollTop / ITEM_HEIGHT) - OVERSCAN);
	$: visibleEnd = Math.min(
		filteredItems.length,
		Math.ceil((listScrollTop + 256) / ITEM_HEIGHT) + OVERSCAN
	);

	const resetView = async () => {
		await tick();

		const selectedInFiltered = filteredItems.findIndex((item) => item.value === value);
		selectedModelIdx = selectedInFiltered >= 0 ? selectedInFiltered : 0;

		const targetScrollTop = Math.max(0, selectedModelIdx * ITEM_HEIGHT - 128 + ITEM_HEIGHT / 2);
		listScrollTop = targetScrollTop;

		await tick();
		if (listContainer) {
			listContainer.scrollTop = targetScrollTop;
		}
	};
</script>

<DropdownMenu.Root
	bind:open={show}
	onOpenChange={async () => {
		listScrollTop = 0;
		resetView();
	}}
	closeFocus={false}
>
	<DropdownMenu.Trigger
		class="relative w-full {($settings?.highContrastMode ?? false)
			? ''
			: 'outline-hidden focus:outline-hidden'}"
		aria-label={selectedModel ? $i18n.t('Selected model: {{modelName}}', { modelName: selectedModel.label }) : placeholder}
		id="model-selector-{id}-button"
	>
		<div
			class="flex w-full text-left px-0.5 bg-transparent truncate {triggerClassName} justify-between {($settings?.highContrastMode ??
			false)
				? 'dark:placeholder-gray-100 placeholder-gray-800'
				: 'placeholder-gray-400'}"
			on:mouseenter={async () => {
				models.set(
					await getModels(
						localStorage.token,
						$config?.features?.enable_direct_connections && ($settings?.directConnections ?? null)
					)
				);
			}}
		>
			{#if selectedModel}
				{selectedModel.label}
			{:else}
				{placeholder}
			{/if}
			<ChevronDown className="self-center ml-2 size-3" strokeWidth="2.5" />
		</div>
	</DropdownMenu.Trigger>

	<DropdownMenu.Content
		class="z-40 {$mobile ? `w-full` : `${className}`} max-w-[calc(100vw-1rem)] justify-start rounded-2xl bg-white dark:bg-gray-850 dark:text-white shadow-lg outline-hidden"
		transition={flyAndScale}
		side={$mobile ? 'bottom' : 'bottom-start'}
		sideOffset={2}
		alignOffset={-1}
	>
		<slot>
			<div class="px-2.5 pt-2.5 pb-1.5 group relative">
				{#if filteredItems.length === 0}
					<div class="flex flex-col items-start justify-center py-6 px-4 text-start">
						<div class="text-sm font-medium text-gray-900 dark:text-gray-100 mb-1">
							{$i18n.t('No models available')}
						</div>
						<div class="text-xs text-gray-500 dark:text-gray-400 mb-4">
							{$i18n.t('Connect to an AI provider to start chatting')}
						</div>
						<a
							href="/admin/settings/connections"
							class="px-4 py-1.5 rounded-xl text-xs font-medium bg-gray-900 dark:bg-white text-white dark:text-gray-900 hover:bg-gray-800 dark:hover:bg-gray-100 transition"
							on:click={() => {
								show = false;
							}}
						>
							{$i18n.t('Manage Connections')}
						</a>
					</div>
				{:else}
					<div
						class="max-h-80 overflow-y-auto"
						role="listbox"
						aria-label={$i18n.t('Available models')}
						bind:this={listContainer}
						on:scroll={() => {
							listScrollTop = listContainer.scrollTop;
						}}
					>
						<div style="height: {visibleStart * ITEM_HEIGHT}px;" />
						{#each filteredItems.slice(visibleStart, visibleEnd) as item, i (item.value)}
							{@const index = visibleStart + i}
							<ModelItem
								{selectedModelIdx}
								{item}
								{index}
								{value}
								onClick={() => {
									value = item.value;
									selectedModelIdx = index;
									show = false;
								}}
							/>
						{/each}
						<div style="height: {(filteredItems.length - visibleEnd) * ITEM_HEIGHT}px;" />
					</div>
				{/if}
			</div>

			<div class="hidden w-[42rem]" />
			<div class="hidden w-[32rem]" />
		</slot>
	</DropdownMenu.Content>
</DropdownMenu.Root>
