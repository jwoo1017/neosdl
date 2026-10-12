import Modal from "./Modal";

export default function ProjectQuickMenuModal({ project, onOpenDetail, onClose }) {
  return (
    <Modal title={project.name} onClose={onClose}>
      <button
        type="button"
        className="btn-primary"
        onClick={() => {
          onOpenDetail(project);
          onClose();
        }}
      >
        상세 보기 들어가기
      </button>
    </Modal>
  );
}
