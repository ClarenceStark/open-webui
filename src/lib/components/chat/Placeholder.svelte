<script lang="ts">
	import { onMount, getContext, createEventDispatcher } from 'svelte';
	import { fade } from 'svelte/transition';

	const dispatch = createEventDispatcher();

	import { getChatList } from '$lib/apis/chats';

	import {
		user,
		WEBUI_NAME,
		models as _models,
		temporaryChatEnabled,
		selectedFolder,
		chats,
		currentChatPage
	} from '$lib/stores';
	import { getModelDisplayName } from '$lib/utils/model-display';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import EyeSlash from '$lib/components/icons/EyeSlash.svelte';
	import MessageInput from './MessageInput.svelte';
	import FolderPlaceholder from './Placeholder/FolderPlaceholder.svelte';
	import FolderTitle from './Placeholder/FolderTitle.svelte';

	const i18n = getContext('i18n');

	export let createMessagePair: Function;
	export let stopResponse: Function;

	export let autoScroll = false;

	export let atSelectedModel: Model | undefined;
	export let selectedModels: [''];

	export let history;

	export let prompt = '';
	export let files = [];
	export let messageInput = null;

	export let selectedToolIds = [];
	export let selectedFilterIds = [];

	export let showCommands = false;

	export let imageGenerationEnabled = false;
	export let codeInterpreterEnabled = false;
	export let webSearchEnabled = false;

	export let onUpload: Function = (e) => {};
	export let onSelect = (e) => {};
	export let onChange = (e) => {};

	export let toolServers = [];

	export let dragged = false;

	let models = [];
	let selectedModelIdx = 0;
	let greetingPeriod = 'Hello';
	let primaryModelName = '';

	const quickActions = [
		{
			label: 'Write',
			prompt: 'Help me write a clear first draft for this idea: '
		},
		{
			label: 'Research',
			prompt: 'Help me research this topic and organize the key takeaways: '
		},
		{
			label: 'Code',
			prompt: 'Help me design or debug this piece of code: '
		},
		{
			label: 'Plan',
			prompt: 'Help me break this into a concrete plan with next steps: '
		}
	];

	$: if (selectedModels.length > 0) {
		selectedModelIdx = models.length - 1;
	}

	$: models = selectedModels.map((id) => $_models.find((m) => m.id === id));
	$: primaryModelName =
		atSelectedModel?.name
			? getModelDisplayName(atSelectedModel.name, atSelectedModel.id)
			: models[selectedModelIdx]?.name
				? getModelDisplayName(models[selectedModelIdx]?.name, models[selectedModelIdx]?.id)
				: $WEBUI_NAME;

	onMount(() => {
		const hour = new Date().getHours();
		greetingPeriod = hour < 12 ? 'Morning' : hour < 18 ? 'Afternoon' : 'Evening';
	});
</script>

<div class="chat-shell-placeholder m-auto w-full max-w-6xl px-2 @2xl:px-20 py-16 text-center">
	{#if $temporaryChatEnabled}
		<Tooltip
			content={$i18n.t("This chat won't appear in history and your messages will not be saved.")}
			className="w-full flex justify-center mb-0.5"
			placement="top"
		>
			<div class="chat-shell-temporary-pill flex items-center gap-2 text-base my-2 w-fit">
				<EyeSlash strokeWidth="2.5" className="size-4" />{$i18n.t('Temporary Chat')}
			</div>
		</Tooltip>
	{/if}

	<div class="w-full text-center flex items-center gap-4 font-primary">
		<div class="w-full flex flex-col justify-center items-center">
			{#if $selectedFolder}
				<FolderTitle
					folder={$selectedFolder}
					onUpdate={async (folder) => {
						await chats.set(await getChatList(localStorage.token, $currentChatPage));
						currentChatPage.set(1);
					}}
					onDelete={async () => {
						await chats.set(await getChatList(localStorage.token, $currentChatPage));
						currentChatPage.set(1);

						selectedFolder.set(null);
					}}
				/>
			{:else}
				<div class="chat-shell-welcome" in:fade={{ duration: 150 }}>
					<div class="chat-shell-welcome-kicker">
						<span class="chat-shell-welcome-dot"></span>
						{primaryModelName}
					</div>
					<h1 class="chat-shell-welcome-title">
						{greetingPeriod}, {$user?.name?.split(' ')?.[0] ?? $WEBUI_NAME}
					</h1>
					<p class="chat-shell-welcome-subtitle">
						Use one workspace to ask, write, research, and ship.
					</p>
				</div>
			{/if}

			<div class="text-base font-normal md:max-w-4xl w-full py-3 {atSelectedModel ? 'mt-2' : ''}">
				<MessageInput
					bind:this={messageInput}
					{history}
					{selectedModels}
					bind:files
					bind:prompt
					bind:autoScroll
					bind:selectedToolIds
					bind:selectedFilterIds
					bind:imageGenerationEnabled
					bind:codeInterpreterEnabled
					bind:webSearchEnabled
					bind:atSelectedModel
					bind:showCommands
					bind:dragged
					{toolServers}
					{stopResponse}
					{createMessagePair}
					hero={true}
					placeholder={$i18n.t('How can I help you today?')}
					{onChange}
					{onUpload}
					on:submit={(e) => {
						dispatch('submit', e.detail);
					}}
				/>
			</div>
		</div>
	</div>

	{#if $selectedFolder}
		<div
			class="mx-auto px-4 md:max-w-3xl md:px-6 font-primary min-h-62"
			in:fade={{ duration: 200, delay: 200 }}
		>
			<FolderPlaceholder folder={$selectedFolder} />
		</div>
	{:else}
		<div class="chat-shell-quick-actions" in:fade={{ duration: 200, delay: 200 }}>
			{#each quickActions as action}
				<button
					type="button"
					class="chat-shell-quick-action"
					on:click={() => onSelect({ type: 'prompt', data: action.prompt })}
				>
					{action.label}
				</button>
			{/each}
		</div>
	{/if}
</div>
