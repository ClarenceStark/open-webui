<script>
	import { getContext } from 'svelte';
	const i18n = getContext('i18n');

	import StatusItem from './StatusHistory/StatusItem.svelte';
	export let statusHistory = [];
	export let expand = false;

	const HIDDEN_STATUS_ACTIONS = new Set(['artifact_uploaded', 'download_artifact', 'view_image']);

	let showHistory = true;

	$: if (expand) {
		showHistory = true;
	} else {
		showHistory = false;
	}

	let history = [];
	let status = null;

	const isVisibleStatus = (item) =>
		item && item.hidden !== true && !HIDDEN_STATUS_ACTIONS.has(item.action);
	let visibleStatusHistory = [];

	$: if (history && history.length > 0) {
		status = history.at(-1);
	} else {
		status = null;
	}

	$: visibleStatusHistory = (statusHistory ?? []).filter(isVisibleStatus);

	$: if (
		visibleStatusHistory.length !== history.length ||
		JSON.stringify(visibleStatusHistory) !== JSON.stringify(history)
	) {
		history = visibleStatusHistory;
	}
</script>

{#if history && history.length > 0}
	{#if status}
		<div class="text-sm flex flex-col w-full">
			<button
				class="w-full"
				aria-label={$i18n.t('Toggle status history')}
				aria-expanded={showHistory}
				on:click={() => {
					showHistory = !showHistory;
				}}
			>
				<div class="flex items-start gap-2">
					<StatusItem {status} />
				</div>
			</button>

			{#if showHistory}
				<div class="flex flex-row">
					{#if history.length > 1}
						<div class="w-full">
							{#each history as status, idx}
								<div class="flex items-stretch gap-2 mb-1">
									<div class=" ">
										<div class="pt-3 px-1 mb-1.5">
											<span class="relative flex size-1.5 rounded-full justify-center items-center">
												<span
													class="relative inline-flex size-1.5 rounded-full bg-gray-500 dark:bg-gray-400"
												></span>
											</span>
										</div>
										{#if idx !== history.length - 1}
											<div
												class="w-[0.5px] ml-[6.5px] h-[calc(100%-14px)] bg-gray-300 dark:bg-gray-700"
											/>
										{/if}
									</div>

									<StatusItem {status} done={true} />
								</div>
							{/each}
						</div>
					{/if}
				</div>
			{/if}
		</div>
	{/if}
{/if}
