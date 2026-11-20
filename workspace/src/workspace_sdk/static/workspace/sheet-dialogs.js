export function initSheetDialogs({
  sheets, getActiveSheet, command, getWorkspace, removeSheet, selectSheet, showCurrentSheet, syncSheets, updateSheetName,
}) {
  const newSheet = document.querySelector("#new-sheet");
  const renameSheet = document.querySelector("#rename-sheet");
  const createDialog = document.querySelector("#create-sheet");
  const createForm = createDialog.querySelector("form");
  const dialogTitle = createForm.querySelector("#create-sheet-title");
  const sheetTitle = createForm.querySelector("#sheet-title");
  const createError = createForm.querySelector("#create-error");
  const createButton = createForm.querySelector('button[type="submit"]');
  const cancelSheet = createForm.querySelector("#cancel-sheet");
  const deleteDialog = document.querySelector("#delete-sheet");
  const deleteForm = deleteDialog.querySelector("form");
  const deleteMessage = deleteForm.querySelector("#delete-sheet-message");
  const deleteError = deleteForm.querySelector(".dialog-error");
  const deleteButton = deleteForm.querySelector('button[type="submit"]');
  const cancelDelete = deleteForm.querySelector('button[type="button"]');
  let sheetToDelete = null;
  let sheetToRename = null;

  function confirmDelete(sheet) {
    sheetToDelete = sheet;
    deleteError.hidden = true;
    deleteMessage.textContent = `“${sheet.name}” will stop if running. Its Python files and history will move to .workspace/deleted-flows.`;
    deleteDialog.showModal();
  }

  cancelDelete.addEventListener("click", () => deleteDialog.close());
  deleteDialog.addEventListener("cancel", event => {
    if (deleteButton.disabled) event.preventDefault();
  });
  deleteForm.addEventListener("submit", async event => {
    event.preventDefault();
    deleteError.hidden = true;
    deleteButton.disabled = cancelDelete.disabled = true;
    try {
      await command(sheetToDelete, "delete");
      removeSheet(sheetToDelete.index);
      showCurrentSheet();
      deleteDialog.close();
      (getActiveSheet()?.tab ?? newSheet).focus();
    } catch (error) {
      deleteError.textContent = error.message;
      deleteError.hidden = false;
    } finally {
      deleteButton.disabled = cancelDelete.disabled = false;
    }
  });

  function openNameDialog(sheet) {
    sheetToRename = sheet;
    createForm.reset();
    createError.hidden = true;
    dialogTitle.textContent = sheet ? "Rename sheet" : "New sheet";
    createButton.textContent = sheet ? "Rename sheet" : "Create sheet";
    sheetTitle.value = sheet?.name ?? "";
    createDialog.showModal();
    if (sheet) sheetTitle.select();
  }

  newSheet.addEventListener("click", () => openNameDialog(null));
  renameSheet.addEventListener("click", () => {
    const sheet = getActiveSheet();
    if (sheet) openNameDialog(sheet);
  });
  cancelSheet.addEventListener("click", () => createDialog.close());
  createDialog.addEventListener("cancel", event => {
    if (createButton.disabled) event.preventDefault();
  });
  createForm.addEventListener("submit", async event => {
    event.preventDefault();
    createError.hidden = true;
    createButton.disabled = cancelSheet.disabled = true;
    try {
      if (sheetToRename) {
        const { name } = await command(sheetToRename, "rename", { name: sheetTitle.value });
        updateSheetName(sheetToRename, name);
        createDialog.close();
        sheetToRename.tab.focus();
        return;
      }
      const response = await fetch("./flows", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: sheetTitle.value }),
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error ?? "Could not create the sheet.");
      syncSheets((await getWorkspace()).flows);
      const sheet = sheets[data.index];
      selectSheet(sheet);
      createDialog.close();
      sheet.tab.focus();
    } catch (error) {
      createError.textContent = error.message;
      createError.hidden = false;
      sheetTitle.focus();
    } finally {
      createButton.disabled = cancelSheet.disabled = false;
    }
  });

  return { confirmDelete };
}
