export enum RegistrationTypeChoiceEnum {
    SOLO = 'solo',
    GROUP = 'group',
}

export enum RegistrationTypeChoiceEnumValues {
    solo = 'Solo Competition',
    group = 'Group Competition Team',
}

export enum RegistrationTypeChoiceEnumValues {
    solo = 'solo',
    group = 'group',
}


export interface CertificateRequest {
    /**
    * @maxLength 255
    */
    name: string;
}

export interface Certificate {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @format uuid
    */
    verificationId?: string;
    enrollment?: number;
    nameOnCertificate?: string;
    fileEn?: File | null;
    fileFa?: File | null;
    isVerified?: boolean | null;
    /**
    * @format date-time
    */
    requestedAt?: string;
    grade?: number | null;
}

export interface CompetitionCertificate {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @format uuid
    */
    verificationId?: string;
    /**
    * @label Registration Type
    */
    registrationType?: RegistrationTypeChoiceEnum;
    /**
    * @label Name on Certificate
    */
    nameOnCertificate?: string;
    ranking?: number | null;
    /**
    * @label English Certificate File
    */
    fileEn?: File | null;
    /**
    * @label Persian Certificate File
    */
    fileFa?: File | null;
    /**
    * @label Verified by Admin?
    */
    isVerified?: boolean;
    /**
    * @format date-time
    */
    requestedAt?: string;
    competitionTitle?: any;
    eventTitle?: any;
}

export interface CompletedEnrollment {
    /**
    * @label ID
    */
    id?: number;
    presentationTitle?: string;
    certificateId?: number | null;
    /**
    * @format uuid
    */
    certificateVerificationId?: string;
    isCertificateVerified?: boolean;
}

export interface EligibleGroupCompetition {
    /**
    * @label ID
    */
    id?: number;
    /**
    * @label Team Name
    * @maxLength 255
    */
    name: string;
    competitionTitle?: string;
    eventTitle?: any;
    certificate?: CompetitionCertificate;
}

export interface EligibleSoloCompetition {
    /**
    * @label ID
    */
    id?: number;
    competitionTitle?: string;
    eventTitle?: any;
    certificate?: CompetitionCertificate;
}

export interface UnifiedCompetitionCertificateRequest {
    registrationType: RegistrationTypeChoiceEnum;
    registrationId: number;
    /**
    * @maxLength 255
    */
    name?: string;
}

