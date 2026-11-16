on run
	try
		set projectFolder to choose folder with prompt "Open Workspace Folder"
		my launchWorkspace(projectFolder)
	on error messageText number errorNumber
		if errorNumber is not -128 then display alert "Could not open Workspace" message messageText as warning
	end try
end run

on open selectedItems
	try
		if (count selectedItems) is not 1 then error "Select one Workspace folder."
		my launchWorkspace(item 1 of selectedItems)
	on error messageText
		display alert "Could not open Workspace" message messageText as warning
	end try
end open

on launchWorkspace(projectFolder)
	set resourcePath to POSIX path of (path to resource "open-workspace")
	with timeout of 300 seconds
		do shell script (quoted form of resourcePath & " " & quoted form of POSIX path of projectFolder)
	end timeout
end launchWorkspace
