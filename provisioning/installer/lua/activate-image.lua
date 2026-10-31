--
-- activate-image.lua: stage and activate every repository of the update store that the data zip was unpacked
-- into, with the kiosk's own update code. Run by Install-Kiosk.ps1 as
--   update-console.exe --store-number:<id> --eval:activate-image.lua
-- It does what the original installer's "Activate Image" step did (Software repo,
-- docs/systems/store-installer.md section 5.2): for each repository UpdateToHead (rebuild every file from its
-- seed and VCDIFF deltas into .staged_), run a staged beforeactivate.lua, ActivateToHead, run afteractivate.lua.
--

local failed = false

for _, repo in pairs( kernel.repositorylist() ) do
	local changes = kernel.repositorypendingchanges( repo )
	kernel.log( "activate-image: " .. repo .. ": " .. #changes .. " pending revisions" )

	if #changes > 0 then
		local result = kernel.repositoryupdate( repo )
		if not result.success then
			kernel.log( "activate-image: repositoryupdate failed for " .. repo )
			failed = true
			break
		end
	end

	local activations = kernel.repositorypendingactivations( repo )
	kernel.log( "activate-image: " .. repo .. ": " .. #activations .. " files to activate" )
	if #activations > 0 then
		local before, after = nil, nil
		for _, f in ipairs( activations ) do
			local lower = string.lower( f )
			if string.find( lower, "beforeactivate.lua" ) ~= nil then before = lower end
			if string.find( lower, "afteractivate.lua" ) ~= nil then after = lower end
		end
		if before ~= nil then
			-- not activated yet: run the staged copy
			local staged = kernel.getdirectoryfrompath( before ) .. "\\.staged_\\beforeactivate.lua"
			kernel.log( "activate-image: running " .. staged )
			kernel.import_script( staged )
		end
		local result = kernel.repositoryactivate( repo )
		if not result.success then
			kernel.log( "activate-image: repositoryactivate failed for " .. repo )
			failed = true
			break
		end
		if after ~= nil then
			kernel.log( "activate-image: running " .. after )
			kernel.import_script( after )
		end
	end
end

if failed then
	kernel.log( "activate-image: stopped on the first failure" )
else
	kernel.log( "activate-image: every repository is at its head" )
	kernel.markscriptcomplete()
end
