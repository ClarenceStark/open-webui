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
	import { getModelShortDescription } from '$lib/utils/model-display';
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

	let greetingKey = 'Hello, {{name}}';
	let models = [];
	let selectedModelIdx = 0;
	let welcomeDescription = 'How can I help you today?';

	$: if (selectedModels.length > 0) {
		selectedModelIdx = models.length - 1;
	}

	$: models = selectedModels.map((id) => $_models.find((m) => m.id === id)).filter(Boolean);
	$: welcomeDescription = getModelShortDescription(
		atSelectedModel ?? models[selectedModelIdx] ?? models[0],
		$i18n.t
	);

	onMount(() => {
		const hour = new Date().getHours();

		const greetingPools: string[][] = [
			// 0–4: 凌晨
			[
				'Still up, {{name}}?',
				'Night owl, {{name}} 🦉',
				'Burning the midnight oil, {{name}}?'
			],
			// 5–8: 清晨
			[
				'Good morning, {{name}}',
				'Rise and shine, {{name}} 🌤',
				'Early bird, {{name}} 🐦'
			],
			// 9–10: 上午
			[
				'Good morning, {{name}}',
				'Morning, {{name}} — what are we building today?',
				'In the zone, {{name}}?'
			],
			// 11–12: 咖啡时间
			[
				'Coffee time, {{name}} ☕',
				'Time for a break, {{name}}?',
				'Refueling, {{name}}?'
			],
			// 13: 午后
			[
				'Afternoon, {{name}}',
				'Post-lunch mode, {{name}} 😴',
				"Afternoon slump? I've got you, {{name}}"
			],
			// 14–16: 下午
			[
				'Good afternoon, {{name}}',
				"Afternoon, {{name}} — let's get things done",
				'Making it happen, {{name}}?'
			],
			// 17–18: 傍晚
			['Evening, {{name}}', 'Wrapping up, {{name}}?', 'Almost there, {{name}} 🌇'],
			// 19–21: 晚上
			[
				'Good evening, {{name}}',
				"Evening, {{name}} — what's on your mind?",
				'Night mode on, {{name}} 🌙'
			],
			// 22–23: 深夜
			['Late night, {{name}}?', 'Still going, {{name}}?', 'Night owl alert, {{name}} 🌙']
		];

		let pool: string[];
		if (hour < 5) pool = greetingPools[0];
		else if (hour < 9) pool = greetingPools[1];
		else if (hour < 11) pool = greetingPools[2];
		else if (hour < 13) pool = greetingPools[3];
		else if (hour < 14) pool = greetingPools[4];
		else if (hour < 17) pool = greetingPools[5];
		else if (hour < 19) pool = greetingPools[6];
		else if (hour < 22) pool = greetingPools[7];
		else pool = greetingPools[8];

		greetingKey = pool[Math.floor(Math.random() * pool.length)];
	});
</script>

<div class="chat-shell-placeholder m-auto w-full max-w-full px-0 py-16 text-center">
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
					<h1 class="chat-shell-welcome-title">
						{$i18n.t(greetingKey, {
							name: $user?.name?.split(' ')?.[0] ?? $WEBUI_NAME
						})}
					</h1>
					<p class="chat-shell-welcome-subtitle">{welcomeDescription}</p>
				</div>
			{/if}

			<div class="chat-shell-input-stage text-base font-normal w-full py-3 {atSelectedModel ? 'mt-2' : ''}">
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
	{/if}
</div>
