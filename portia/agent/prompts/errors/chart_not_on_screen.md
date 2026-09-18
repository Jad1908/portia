The window has not painted a chart on a tab called `{tab}`.

Either no chart has that name, or its tab is not the one showing. A tab is painted only
while it is on screen: a closed tab, or one behind another tab, has no picture.

- Check the name against the `drawn` field of your `plot_data` receipt. It has to match
  exactly.
- If the name is right, the user has moved away from it. Drawing it again with
  `plot_data` under the same name brings it to the front, or you can ask the user to
  open the tab.

Do not describe how the chart looks until you have seen it.
