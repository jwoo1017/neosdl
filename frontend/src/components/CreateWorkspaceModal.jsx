import Modal from "./Modal";
import WorkspaceCreateForm from "./WorkspaceCreateForm";

export default function CreateWorkspaceModal({ onCreated, onClose }) {
  return (
    <Modal title="새 워크스페이스" onClose={onClose}>
      <WorkspaceCreateForm
        onCreated={(workspace) => {
          onCreated(workspace);
          onClose();
        }}
      />
    </Modal>
  );
}
