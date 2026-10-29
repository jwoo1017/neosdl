import { SPACE_ROLE_LABEL } from "../spaceRoles";
import { BackendIcon, DoorKeyIcon, EyeOffIcon, FrontendIcon, GlobeIcon, PmIcon } from "../icons/SpaceIcons";

// 워크스페이스·팀스페이스 관리 모달이 Segmented/ChoiceCards 에 넘기는 선택지.

export const VISIBILITY_OPTIONS = [
  { value: "open", Icon: GlobeIcon, title: "공개", desc: "워크스페이스 멤버 누구나 보고 가입할 수 있습니다." },
  { value: "closed", Icon: DoorKeyIcon, title: "참가 제한", desc: "누구나 볼 수 있지만 가입은 초대로만 됩니다." },
  { value: "private", Icon: EyeOffIcon, title: "비공개", desc: "멤버에게만 보입니다." },
];

export const DISCIPLINE_LABEL = { pm: "PM", frontend: "프론트엔드", backend: "백엔드" };

export const DISCIPLINE_OPTIONS = [
  { value: "pm", label: "PM", Icon: PmIcon },
  { value: "frontend", label: "프론트", Icon: FrontendIcon },
  { value: "backend", label: "백엔드", Icon: BackendIcon },
];

export const roleOptions = (roles) => roles.map((r) => ({ value: r, label: SPACE_ROLE_LABEL[r] }));
