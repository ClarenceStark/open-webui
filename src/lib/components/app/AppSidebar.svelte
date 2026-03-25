<script lang="ts">
	import { goto } from '$app/navigation';
	import { onMount } from 'svelte';
	import Tooltip from '$lib/components/common/Tooltip.svelte';
	import Plus from '$lib/components/icons/Plus.svelte';
	import { WEBUI_BASE_URL } from '$lib/constants';

	let selected = '';

	const syncSelected = () => {
		selected = window.location.pathname === '/' ? '' : 'home';
	};

	onMount(() => {
		syncSelected();
		window.addEventListener('popstate', syncSelected);

		return () => {
			window.removeEventListener('popstate', syncSelected);
		};
	});
</script>

<nav
	aria-label="App navigation"
	class="min-w-[4.5rem] bg-gray-50 dark:bg-gray-950 flex gap-2.5 flex-col pt-8 pwa-safe-sidebar"
>
	<div class="flex justify-center relative">
		{#if selected === 'home'}
			<div class="absolute top-0 left-0 flex h-full">
				<div class="my-auto rounded-r-lg w-1 h-8 bg-black dark:bg-white"></div>
			</div>
		{/if}

		<Tooltip content="Home" placement="right">
			<button
				aria-label="Home"
				class=" cursor-pointer {selected === 'home' ? 'rounded-2xl' : 'rounded-full'}"
				on:click={async () => {
					selected = 'home';

					if (window.electronAPI) {
						window.electronAPI.load('home');
						return;
					}

					await goto('/workspace');
				}}
			>
				<img
					src="{WEBUI_BASE_URL}/openai-mark.svg"
					class="size-11 p-0.5 dark:invert"
					alt="logo"
					draggable="false"
				/>
			</button>
		</Tooltip>
	</div>

	<div class=" -mt-1 border-[1.5px] border-gray-100 dark:border-gray-900 mx-4"></div>

	<div class="flex justify-center relative group">
		{#if selected === ''}
			<div class="absolute top-0 left-0 flex h-full">
				<div class="my-auto rounded-r-lg w-1 h-8 bg-black dark:bg-white"></div>
			</div>
		{/if}
		<button
			aria-label="Chat"
			class=" cursor-pointer bg-transparent"
			on:click={async () => {
				selected = '';
				await goto('/');
			}}
		>
			<img
				src="{WEBUI_BASE_URL}/openai-mark.svg"
				class="size-10 dark:invert {selected === '' ? 'rounded-2xl' : 'rounded-full'}"
				alt="logo"
				draggable="false"
			/>
		</button>
	</div>

	<!-- <div class="flex justify-center relative group text-gray-400">
		<button class=" cursor-pointer p-2" on:click={() => {}}>
			<Plus className="size-4" strokeWidth="2" />
		</button>
	</div> -->
</nav>
